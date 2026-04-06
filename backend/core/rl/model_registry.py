"""
RL 모델 레지스트리 (Model Registry & Version Management)

학습된 RL 모델의 버전 관리, 메타데이터 저장, 자동 배포를 담당합니다.

주요 기능:
- 모델 등록: 학습 결과 + 메타데이터를 함께 저장
- 버전 관리: 시간순 버전 자동 생성 (v001, v002, ...)
- Champion 선정: OOS Sharpe 기준 최고 성능 모델 자동 지정
- 모델 로드: champion 또는 특정 버전 로드
- 이력 조회: 전체 모델 이력 + 성능 비교

디렉토리 구조:
    models/
    └── registry/
        ├── manifest.json          # 전체 모델 목록 + champion 정보
        ├── v001/
        │   ├── model.zip          # SB3 모델 파일
        │   └── metadata.json      # 학습 설정, 성능 메트릭, 데이터 정보
        ├── v002/
        │   ├── model.zip
        │   └── metadata.json
        └── ...

사용법:
    registry = ModelRegistry("models/registry")

    # 모델 등록
    version = registry.register(model, algorithm="PPO", eval_result=eval_result,
                                 config=config, data_info={"ticker": "005930", ...})

    # Champion 로드
    model, meta = registry.load_champion()

    # 특정 버전 로드
    model, meta = registry.load_version("v003")

    # 이력 조회
    history = registry.list_versions()
"""

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from stable_baselines3 import PPO, SAC

from config.logging import logger


@dataclass
class ModelMetadata:
    """모델 메타데이터"""

    version: str
    algorithm: str
    created_at: str

    # 학습 정보
    total_timesteps: int = 0
    training_time_seconds: float = 0.0
    episode_count: int = 0

    # 성능 메트릭
    oos_sharpe: float = 0.0
    oos_return: float = 0.0
    oos_max_drawdown: float = 0.0
    oos_total_trades: int = 0
    improvement_vs_baseline: float = 0.0

    # 데이터 정보
    data_source: str = ""
    tickers: list[str] | None = None
    train_start: str = ""
    train_end: str = ""
    train_samples: int = 0

    # 설정 스냅샷
    config_snapshot: dict[str, Any] | None = None

    # Champion 여부
    is_champion: bool = False
    champion_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ModelMetadata":
        # dataclass 필드만 추출
        valid_keys = set(cls.__dataclass_fields__.keys())
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


class ModelRegistry:
    """
    RL 모델 버전 관리 레지스트리

    모든 모델을 시간순으로 버전 관리하며,
    OOS Sharpe 기준 최고 성능 모델을 champion으로 자동 지정합니다.
    """

    MANIFEST_FILE = "manifest.json"
    MODEL_FILE = "model.zip"
    METADATA_FILE = "metadata.json"

    def __init__(self, registry_dir: str = "models/registry"):
        self.registry_dir = Path(registry_dir)
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        self._manifest = self._load_manifest()

    def register(
        self,
        model,
        algorithm: str,
        eval_result=None,
        config=None,
        data_info: dict | None = None,
        train_result=None,
    ) -> str:
        """
        학습된 모델을 레지스트리에 등록

        Args:
            model: SB3 모델 객체
            algorithm: "PPO" 또는 "SAC"
            eval_result: EvalResult (평가 메트릭)
            config: RLConfig (학습 설정)
            data_info: 데이터 정보 dict (ticker, source, date range 등)
            train_result: TrainResult (학습 결과)

        Returns:
            version: 등록된 버전 문자열 (예: "v001")
        """
        version = self._next_version()
        version_dir = self.registry_dir / version
        version_dir.mkdir(parents=True, exist_ok=True)

        # 1. 모델 저장
        model_path = version_dir / self.MODEL_FILE
        model.save(str(model_path).replace(".zip", ""))

        # 2. 메타데이터 생성
        meta = ModelMetadata(
            version=version,
            algorithm=algorithm,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        # 학습 정보
        if train_result is not None:
            meta.total_timesteps = train_result.total_timesteps
            meta.training_time_seconds = train_result.training_time_seconds
            meta.episode_count = len(train_result.episode_rewards)

        # 평가 메트릭
        if eval_result is not None:
            meta.oos_sharpe = eval_result.sharpe_ratio
            meta.oos_return = eval_result.total_return
            meta.oos_max_drawdown = eval_result.max_drawdown
            meta.oos_total_trades = eval_result.total_trades
            meta.improvement_vs_baseline = eval_result.improvement_pct

        # 데이터 정보
        if data_info:
            meta.data_source = data_info.get("source", "")
            meta.tickers = data_info.get("tickers", [])
            meta.train_start = data_info.get("start", "")
            meta.train_end = data_info.get("end", "")
            meta.train_samples = data_info.get("samples", 0)

        # 설정 스냅샷
        if config is not None:
            meta.config_snapshot = asdict(config)

        # Champion 판정
        current_champion = self._manifest.get("champion_version")
        if current_champion is None:
            # 첫 모델은 자동 champion
            meta.is_champion = True
            meta.champion_reason = "first_model"
        else:
            # 기존 champion보다 Sharpe가 높으면 교체
            champion_meta = self._load_metadata(current_champion)
            if champion_meta and meta.oos_sharpe > champion_meta.oos_sharpe:
                meta.is_champion = True
                meta.champion_reason = f"sharpe_improvement: " f"{champion_meta.oos_sharpe:.4f} → {meta.oos_sharpe:.4f}"
                # 이전 champion 해제
                champion_meta.is_champion = False
                self._save_metadata(current_champion, champion_meta)

        # 3. 메타데이터 저장
        self._save_metadata(version, meta)

        # 4. Manifest 업데이트
        self._manifest["versions"].append(version)
        self._manifest["latest_version"] = version
        if meta.is_champion:
            self._manifest["champion_version"] = version
            self._manifest["champion_sharpe"] = meta.oos_sharpe
        self._manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._save_manifest()

        status = "champion" if meta.is_champion else "registered"
        logger.info(
            f"[ModelRegistry] {version} {status}: " f"sharpe={meta.oos_sharpe:.4f}, return={meta.oos_return:.2%}"
        )

        return version

    def load_champion(self) -> tuple[Any, ModelMetadata] | None:
        """
        Champion 모델 로드

        Returns:
            (model, metadata) 또는 champion이 없으면 None
        """
        champion_ver = self._manifest.get("champion_version")
        if not champion_ver:
            logger.warning("[ModelRegistry] No champion model registered")
            return None
        return self.load_version(champion_ver)

    def load_version(self, version: str) -> tuple[Any, ModelMetadata] | None:
        """
        특정 버전 모델 로드

        Args:
            version: 버전 문자열 (예: "v001")

        Returns:
            (model, metadata) 또는 없으면 None
        """
        version_dir = self.registry_dir / version
        model_path = version_dir / self.MODEL_FILE

        if not model_path.exists():
            # .zip 없이 저장된 경우
            alt_path = version_dir / "model"
            if not alt_path.exists():
                logger.error(f"[ModelRegistry] Model not found: {version}")
                return None
            model_path = alt_path

        meta = self._load_metadata(version)
        if meta is None:
            return None

        # 알고리즘에 따라 로드
        model_cls = PPO if meta.algorithm == "PPO" else SAC
        model = model_cls.load(str(model_path).replace(".zip", ""))

        logger.info(f"[ModelRegistry] Loaded {version} ({meta.algorithm})")
        return model, meta

    def list_versions(self) -> list[ModelMetadata]:
        """전체 모델 이력 조회 (최신순)"""
        versions = self._manifest.get("versions", [])
        result = []
        for ver in reversed(versions):
            meta = self._load_metadata(ver)
            if meta:
                result.append(meta)
        return result

    def get_champion_version(self) -> str | None:
        """현재 champion 버전 반환"""
        return self._manifest.get("champion_version")

    def promote_to_champion(self, version: str) -> bool:
        """
        수동으로 특정 버전을 champion으로 승격

        Args:
            version: 승격할 버전

        Returns:
            성공 여부
        """
        meta = self._load_metadata(version)
        if meta is None:
            return False

        # 기존 champion 해제
        old_champion = self._manifest.get("champion_version")
        if old_champion and old_champion != version:
            old_meta = self._load_metadata(old_champion)
            if old_meta:
                old_meta.is_champion = False
                self._save_metadata(old_champion, old_meta)

        # 새 champion 지정
        meta.is_champion = True
        meta.champion_reason = "manual_promotion"
        self._save_metadata(version, meta)

        self._manifest["champion_version"] = version
        self._manifest["champion_sharpe"] = meta.oos_sharpe
        self._manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._save_manifest()

        logger.info(f"[ModelRegistry] {version} promoted to champion (manual)")
        return True

    def delete_version(self, version: str) -> bool:
        """
        특정 버전 삭제 (champion은 삭제 불가)

        Args:
            version: 삭제할 버전

        Returns:
            성공 여부
        """
        if version == self._manifest.get("champion_version"):
            logger.error(f"[ModelRegistry] Cannot delete champion: {version}")
            return False

        version_dir = self.registry_dir / version
        if version_dir.exists():
            shutil.rmtree(version_dir)

        if version in self._manifest.get("versions", []):
            self._manifest["versions"].remove(version)
            self._save_manifest()

        logger.info(f"[ModelRegistry] Deleted {version}")
        return True

    # ── 내부 메서드 ──

    def _next_version(self) -> str:
        """다음 버전 번호 생성"""
        versions = self._manifest.get("versions", [])
        if not versions:
            return "v001"
        last = versions[-1]
        num = int(last[1:]) + 1
        return f"v{num:03d}"

    def _load_manifest(self) -> dict:
        """manifest.json 로드"""
        path = self.registry_dir / self.MANIFEST_FILE
        if path.exists():
            return json.loads(path.read_text())
        return {
            "versions": [],
            "champion_version": None,
            "champion_sharpe": None,
            "latest_version": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _save_manifest(self):
        """manifest.json 저장"""
        path = self.registry_dir / self.MANIFEST_FILE
        path.write_text(json.dumps(self._manifest, indent=2, ensure_ascii=False))

    def _load_metadata(self, version: str) -> ModelMetadata | None:
        """특정 버전 메타데이터 로드"""
        path = self.registry_dir / version / self.METADATA_FILE
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return ModelMetadata.from_dict(data)

    def _save_metadata(self, version: str, meta: ModelMetadata):
        """메타데이터 저장"""
        version_dir = self.registry_dir / version
        version_dir.mkdir(parents=True, exist_ok=True)
        path = version_dir / self.METADATA_FILE
        path.write_text(json.dumps(meta.to_dict(), indent=2, ensure_ascii=False))
