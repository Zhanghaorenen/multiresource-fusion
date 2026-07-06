"""
多源异构数据时空对齐与融合算法
------------------------------------------------
当前代码将原来的图文情感分类任务，改造为：
1. 多源数据导入与预处理
2. 多模态数据时空对齐
3. 多模态融合判别与结果输出

支持模态：温度图片/温度表、振动数据、音频数据、视频帧/视频特征。

运行方式：
    python main_multisource_fusion.py --make_demo
    python main_multisource_fusion.py --data_dir ./data --output_dir ./output

推荐数据目录结构：
    data/
    ├── thermal.csv        # 温度图片或温度特征表
    ├── vibration.csv      # 振动时间序列或振动特征表
    ├── audio.csv          # 音频文件清单或音频特征表
    ├── video.csv          # 视频帧/目标检测特征表
    └── spatial_map.csv    # 设备、区域、传感器映射表

输出目录：
    output/
    ├── aligned_records.csv
    ├── fusion_result.csv
    └── fusion_report.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import wave
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    from PIL import Image
except Exception:
    Image = None


# =============================
# 一、配置参数
# =============================

@dataclass
class FusionConfig:
    """算法参数配置。"""

    data_dir: str = "./data"
    output_dir: str = "./output"

    thermal_file: str = "thermal.csv"
    vibration_file: str = "vibration.csv"
    audio_file: str = "audio.csv"
    video_file: str = "video.csv"
    spatial_map_file: str = "spatial_map.csv"

    # 时空对齐参数
    time_window_seconds: float = 1.0

    # 各模态融合权重
    thermal_weight: float = 0.25
    vibration_weight: float = 0.30
    audio_weight: float = 0.20
    video_weight: float = 0.25

    # 判别阈值
    suspicious_threshold: float = 0.50
    abnormal_threshold: float = 0.70

    # 单模态异常阈值，可根据实验数据调整
    thermal_high_threshold: float = 80.0       # 最高温度阈值
    vibration_rms_threshold: float = 1.20      # 振动 RMS 阈值
    audio_energy_threshold: float = 0.60       # 音频能量阈值
    video_motion_threshold: float = 0.60       # 视频运动/异常分数阈值


MODALITY_COLUMNS = ["thermal", "vibration", "audio", "video"]


# =============================
# 二、通用工具函数
# =============================

def ensure_dir(path: str | Path) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def parse_time_column(df: pd.DataFrame, candidates: List[str]) -> Tuple[pd.DataFrame, str]:
    """从候选列中寻找时间列，并统一转换为 datetime。"""
    for col in candidates:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col])
            return df, col
    raise ValueError(f"未找到时间列，候选列为：{candidates}，当前列为：{list(df.columns)}")


def safe_read_csv(path: str | Path, required: bool = True) -> pd.DataFrame:
    """安全读取 CSV。"""
    path = Path(path)
    if not path.exists():
        if required:
            raise FileNotFoundError(f"文件不存在：{path}")
        return pd.DataFrame()
    return pd.read_csv(path)


def normalize_score(value: float, threshold: float, max_ratio: float = 2.0) -> float:
    """将指标按阈值归一化为 0~1 异常分数。"""
    if threshold <= 0 or pd.isna(value):
        return 0.0
    score = value / (threshold * max_ratio)
    return float(np.clip(score, 0.0, 1.0))


def kurtosis_np(x: np.ndarray) -> float:
    """计算峭度，避免依赖 scipy。"""
    x = np.asarray(x, dtype=float)
    if len(x) == 0:
        return 0.0
    std = np.std(x)
    if std == 0:
        return 0.0
    z = (x - np.mean(x)) / std
    return float(np.mean(z ** 4))


def dominant_frequency(values: np.ndarray, sample_rate: Optional[float]) -> float:
    """计算主频。如果没有采样率，则返回 0。"""
    values = np.asarray(values, dtype=float)
    if sample_rate is None or sample_rate <= 0 or len(values) < 2:
        return 0.0
    values = values - np.mean(values)
    spectrum = np.abs(np.fft.rfft(values))
    freqs = np.fft.rfftfreq(len(values), d=1.0 / sample_rate)
    if len(spectrum) <= 1:
        return 0.0
    idx = int(np.argmax(spectrum[1:]) + 1)
    return float(freqs[idx])


def read_wav_basic_features(path: str | Path) -> Dict[str, float]:
    """读取 wav 文件并提取短时能量、过零率等基础特征。"""
    path = Path(path)
    if not path.exists():
        return {"audio_energy": 0.0, "zero_crossing_rate": 0.0, "audio_duration": 0.0}

    with wave.open(str(path), "rb") as wf:
        n_channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        frame_rate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    if sample_width == 1:
        dtype = np.uint8
        data = np.frombuffer(raw, dtype=dtype).astype(np.float32) - 128
    elif sample_width == 2:
        dtype = np.int16
        data = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    elif sample_width == 4:
        dtype = np.int32
        data = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    else:
        return {"audio_energy": 0.0, "zero_crossing_rate": 0.0, "audio_duration": 0.0}

    if n_channels > 1:
        data = data.reshape(-1, n_channels).mean(axis=1)

    if len(data) == 0:
        return {"audio_energy": 0.0, "zero_crossing_rate": 0.0, "audio_duration": 0.0}

    data = data / (np.max(np.abs(data)) + 1e-8)
    energy = float(np.mean(data ** 2))
    zcr = float(np.mean(np.abs(np.diff(np.signbit(data))).astype(float)))
    duration = float(n_frames / frame_rate) if frame_rate else 0.0
    return {"audio_energy": energy, "zero_crossing_rate": zcr, "audio_duration": duration}


def read_image_temperature_proxy(path: str | Path) -> Dict[str, float]:
    """
    读取温度图片。如果图片本身不是温度矩阵，则用灰度强度近似热度分布。
    实际项目中若有真实温度矩阵，应优先在 thermal.csv 中提供 max_temp/mean_temp。
    """
    if Image is None:
        return {"mean_temp": 0.0, "max_temp": 0.0, "temp_std": 0.0, "hot_area_ratio": 0.0}

    path = Path(path)
    if not path.exists():
        return {"mean_temp": 0.0, "max_temp": 0.0, "temp_std": 0.0, "hot_area_ratio": 0.0}

    img = Image.open(path).convert("L")
    arr = np.asarray(img, dtype=np.float32)
    # 将 0~255 灰度近似映射为 20~100 摄氏度，仅用于无真实温度矩阵时的占位特征
    temp = 20.0 + arr / 255.0 * 80.0
    return {
        "mean_temp": float(np.mean(temp)),
        "max_temp": float(np.max(temp)),
        "temp_std": float(np.std(temp)),
        "hot_area_ratio": float(np.mean(temp >= 80.0)),
    }


# =============================
# 三、多源数据导入与预处理
# =============================

class MultiSourceDataLoader:
    """多源数据导入与预处理。"""

    def __init__(self, config: FusionConfig):
        self.config = config
        self.data_dir = Path(config.data_dir)

    def load_all(self) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        thermal = self.load_thermal()
        vibration = self.load_vibration()
        audio = self.load_audio()
        video = self.load_video()
        spatial_map = self.load_spatial_map()
        return thermal, vibration, audio, video, spatial_map

    def load_thermal(self) -> pd.DataFrame:
        df = safe_read_csv(self.data_dir / self.config.thermal_file)
        df, time_col = parse_time_column(df, ["timestamp", "capture_time", "time", "frame_time"])
        df = df.rename(columns={time_col: "timestamp"})

        # 兼容字段
        if "device_id" not in df.columns:
            df["device_id"] = "UNKNOWN_DEVICE"
        if "roi_id" not in df.columns:
            df["roi_id"] = df.get("region_id", "UNKNOWN_ROI")

        # 如果没有温度统计特征，但存在图片路径，则从图片中提取近似特征
        for col in ["mean_temp", "max_temp", "temp_std", "hot_area_ratio"]:
            if col not in df.columns:
                df[col] = np.nan

        if "image_path" in df.columns:
            for idx, row in df.iterrows():
                if pd.isna(row.get("max_temp")):
                    image_path = self.data_dir / str(row["image_path"])
                    features = read_image_temperature_proxy(image_path)
                    for key, value in features.items():
                        df.at[idx, key] = value

        df["thermal_score"] = df["max_temp"].apply(
            lambda x: normalize_score(x, self.config.thermal_high_threshold)
        )
        return df

    def load_vibration(self) -> pd.DataFrame:
        df = safe_read_csv(self.data_dir / self.config.vibration_file)
        df, time_col = parse_time_column(df, ["timestamp", "time", "sample_time"])
        df = df.rename(columns={time_col: "timestamp"})

        if "device_id" not in df.columns:
            df["device_id"] = "UNKNOWN_DEVICE"
        if "sensor_id" not in df.columns:
            df["sensor_id"] = "UNKNOWN_VIB"

        # 原始振动值字段兼容
        value_col = None
        for candidate in ["value", "acceleration", "vibration", "amplitude"]:
            if candidate in df.columns:
                value_col = candidate
                break

        # 若已提供特征，直接使用；否则保留原始值，后续在时间窗口内统计
        for col in ["vibration_rms", "vibration_peak", "vibration_kurtosis", "dominant_freq"]:
            if col not in df.columns:
                df[col] = np.nan

        if value_col is not None:
            df = df.rename(columns={value_col: "vibration_value"})
        elif "vibration_value" not in df.columns:
            df["vibration_value"] = np.nan

        return df

    def load_audio(self) -> pd.DataFrame:
        df = safe_read_csv(self.data_dir / self.config.audio_file)
        df, time_col = parse_time_column(df, ["timestamp", "start_time", "time", "capture_time"])
        df = df.rename(columns={time_col: "timestamp"})

        if "device_id" not in df.columns:
            df["device_id"] = "UNKNOWN_DEVICE"
        if "sensor_id" not in df.columns:
            df["sensor_id"] = "UNKNOWN_MIC"

        for col in ["audio_energy", "zero_crossing_rate", "audio_duration"]:
            if col not in df.columns:
                df[col] = np.nan

        if "audio_path" in df.columns:
            for idx, row in df.iterrows():
                if pd.isna(row.get("audio_energy")):
                    audio_path = self.data_dir / str(row["audio_path"])
                    features = read_wav_basic_features(audio_path)
                    for key, value in features.items():
                        df.at[idx, key] = value

        df["audio_score"] = df["audio_energy"].apply(
            lambda x: normalize_score(x, self.config.audio_energy_threshold)
        )
        return df

    def load_video(self) -> pd.DataFrame:
        df = safe_read_csv(self.data_dir / self.config.video_file)
        df, time_col = parse_time_column(df, ["timestamp", "frame_time", "time", "capture_time"])
        df = df.rename(columns={time_col: "timestamp"})

        if "device_id" not in df.columns:
            df["device_id"] = "UNKNOWN_DEVICE"
        if "roi_id" not in df.columns:
            df["roi_id"] = df.get("region_id", "UNKNOWN_ROI")

        # 视频中可以是检测框、运动分数、目标状态等
        if "motion_score" not in df.columns:
            df["motion_score"] = df.get("video_score", 0.0)
        if "object_state" not in df.columns:
            df["object_state"] = "unknown"

        df["video_score"] = df["motion_score"].apply(
            lambda x: normalize_score(x, self.config.video_motion_threshold)
        )
        return df

    def load_spatial_map(self) -> pd.DataFrame:
        df = safe_read_csv(self.data_dir / self.config.spatial_map_file)
        required_cols = ["device_id", "thermal_roi", "video_roi", "vibration_sensor", "audio_sensor"]
        for col in required_cols:
            if col not in df.columns:
                raise ValueError(f"空间映射表缺少字段：{col}，当前字段：{list(df.columns)}")
        return df


# =============================
# 四、时空对齐算法
# =============================

class SpatioTemporalAligner:
    """基于统一时间窗口和空间映射表的多模态时空对齐。"""

    def __init__(self, config: FusionConfig):
        self.config = config
        self.delta = pd.Timedelta(seconds=config.time_window_seconds)

    def align(
        self,
        thermal: pd.DataFrame,
        vibration: pd.DataFrame,
        audio: pd.DataFrame,
        video: pd.DataFrame,
        spatial_map: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        以视频帧/目标检测时间作为基准事件时间。
        如果实际项目中有独立事件表，可以将 video 替换为 event 表。
        """
        records: List[Dict] = []
        video_sorted = video.sort_values("timestamp").reset_index(drop=True)

        for _, event in video_sorted.iterrows():
            event_time = event["timestamp"]
            device_id = event["device_id"]
            mapping = spatial_map[spatial_map["device_id"] == device_id]
            if mapping.empty:
                continue
            mapping_row = mapping.iloc[0]

            thermal_match = self._match_thermal(thermal, event_time, mapping_row)
            vibration_match = self._match_vibration(vibration, event_time, mapping_row)
            audio_match = self._match_audio(audio, event_time, mapping_row)

            time_match = all([
                thermal_match is not None,
                vibration_match is not None,
                audio_match is not None,
            ])
            space_match = True  # 能通过映射表取到数据即认为空间匹配成功
            aligned = bool(time_match and space_match)

            record = {
                "event_time": event_time,
                "device_id": device_id,
                "video_roi": event.get("roi_id", ""),
                "video_state": event.get("object_state", "unknown"),
                "video_score": float(event.get("video_score", 0.0)),
                "time_match": "成功" if time_match else "失败",
                "space_match": "成功" if space_match else "失败",
                "alignment_result": "对齐成功" if aligned else "对齐失败",
            }

            if thermal_match is not None:
                record.update({
                    "thermal_timestamp": thermal_match.get("timestamp"),
                    "thermal_roi": thermal_match.get("roi_id", ""),
                    "mean_temp": float(thermal_match.get("mean_temp", 0.0)),
                    "max_temp": float(thermal_match.get("max_temp", 0.0)),
                    "temp_std": float(thermal_match.get("temp_std", 0.0)),
                    "hot_area_ratio": float(thermal_match.get("hot_area_ratio", 0.0)),
                    "thermal_score": float(thermal_match.get("thermal_score", 0.0)),
                })
            else:
                record.update(self._empty_thermal())

            if vibration_match is not None:
                record.update(vibration_match)
            else:
                record.update(self._empty_vibration())

            if audio_match is not None:
                record.update({
                    "audio_timestamp": audio_match.get("timestamp"),
                    "audio_sensor": audio_match.get("sensor_id", ""),
                    "audio_energy": float(audio_match.get("audio_energy", 0.0)),
                    "zero_crossing_rate": float(audio_match.get("zero_crossing_rate", 0.0)),
                    "audio_score": float(audio_match.get("audio_score", 0.0)),
                })
            else:
                record.update(self._empty_audio())

            records.append(record)

        return pd.DataFrame(records)

    def _time_filter(self, df: pd.DataFrame, event_time: pd.Timestamp) -> pd.DataFrame:
        return df[(df["timestamp"] >= event_time - self.delta) & (df["timestamp"] <= event_time + self.delta)]

    def _match_thermal(self, thermal: pd.DataFrame, event_time: pd.Timestamp, mapping_row: pd.Series) -> Optional[pd.Series]:
        candidates = self._time_filter(thermal, event_time)
        candidates = candidates[candidates["roi_id"].astype(str) == str(mapping_row["thermal_roi"])]
        if candidates.empty:
            return None
        idx = (candidates["timestamp"] - event_time).abs().idxmin()
        return candidates.loc[idx]

    def _match_vibration(self, vibration: pd.DataFrame, event_time: pd.Timestamp, mapping_row: pd.Series) -> Optional[Dict]:
        candidates = self._time_filter(vibration, event_time)
        candidates = candidates[candidates["sensor_id"].astype(str) == str(mapping_row["vibration_sensor"])]
        if candidates.empty:
            return None

        # 如果是原始振动值，则在窗口内统计特征
        values = candidates["vibration_value"].dropna().to_numpy(dtype=float)
        if len(values) > 0:
            time_span = (candidates["timestamp"].max() - candidates["timestamp"].min()).total_seconds()
            sample_rate = len(candidates) / time_span if time_span > 0 else None
            rms = float(np.sqrt(np.mean(values ** 2)))
            peak = float(np.max(np.abs(values)))
            kurt = kurtosis_np(values)
            main_freq = dominant_frequency(values, sample_rate)
        else:
            row = candidates.iloc[0]
            rms = float(row.get("vibration_rms", 0.0)) if not pd.isna(row.get("vibration_rms", np.nan)) else 0.0
            peak = float(row.get("vibration_peak", 0.0)) if not pd.isna(row.get("vibration_peak", np.nan)) else 0.0
            kurt = float(row.get("vibration_kurtosis", 0.0)) if not pd.isna(row.get("vibration_kurtosis", np.nan)) else 0.0
            main_freq = float(row.get("dominant_freq", 0.0)) if not pd.isna(row.get("dominant_freq", np.nan)) else 0.0

        vibration_score = normalize_score(rms, self.config.vibration_rms_threshold)
        return {
            "vibration_timestamp_start": candidates["timestamp"].min(),
            "vibration_timestamp_end": candidates["timestamp"].max(),
            "vibration_sensor": str(mapping_row["vibration_sensor"]),
            "vibration_rms": rms,
            "vibration_peak": peak,
            "vibration_kurtosis": kurt,
            "dominant_freq": main_freq,
            "vibration_score": vibration_score,
        }

    def _match_audio(self, audio: pd.DataFrame, event_time: pd.Timestamp, mapping_row: pd.Series) -> Optional[pd.Series]:
        candidates = self._time_filter(audio, event_time)
        candidates = candidates[candidates["sensor_id"].astype(str) == str(mapping_row["audio_sensor"])]
        if candidates.empty:
            return None
        idx = (candidates["timestamp"] - event_time).abs().idxmin()
        return candidates.loc[idx]

    @staticmethod
    def _empty_thermal() -> Dict:
        return {
            "thermal_timestamp": None,
            "thermal_roi": "",
            "mean_temp": 0.0,
            "max_temp": 0.0,
            "temp_std": 0.0,
            "hot_area_ratio": 0.0,
            "thermal_score": 0.0,
        }

    @staticmethod
    def _empty_vibration() -> Dict:
        return {
            "vibration_timestamp_start": None,
            "vibration_timestamp_end": None,
            "vibration_sensor": "",
            "vibration_rms": 0.0,
            "vibration_peak": 0.0,
            "vibration_kurtosis": 0.0,
            "dominant_freq": 0.0,
            "vibration_score": 0.0,
        }

    @staticmethod
    def _empty_audio() -> Dict:
        return {
            "audio_timestamp": None,
            "audio_sensor": "",
            "audio_energy": 0.0,
            "zero_crossing_rate": 0.0,
            "audio_score": 0.0,
        }


# =============================
# 五、多模态融合判别
# =============================

class MultiModalFusionDecision:
    """多模态加权融合判别。"""

    def __init__(self, config: FusionConfig):
        self.config = config
        self.weights = {
            "thermal_score": config.thermal_weight,
            "vibration_score": config.vibration_weight,
            "audio_score": config.audio_weight,
            "video_score": config.video_weight,
        }
        total = sum(self.weights.values())
        if total <= 0:
            raise ValueError("融合权重之和必须大于 0")
        self.weights = {k: v / total for k, v in self.weights.items()}

    def predict(self, aligned_df: pd.DataFrame) -> pd.DataFrame:
        if aligned_df.empty:
            return aligned_df

        result = aligned_df.copy()
        result["fusion_score"] = result.apply(self._weighted_score, axis=1)
        result["final_result"] = result["fusion_score"].apply(self._label_by_score)
        result["confidence"] = result["fusion_score"].apply(lambda x: round(float(x) * 100, 2))
        result["abnormal_sources"] = result.apply(self._abnormal_sources, axis=1)
        return result

    def _weighted_score(self, row: pd.Series) -> float:
        score = 0.0
        for col, weight in self.weights.items():
            score += float(row.get(col, 0.0)) * weight
        # 如果时空对齐失败，降低融合可信度
        if row.get("alignment_result") != "对齐成功":
            score *= 0.60
        return round(float(np.clip(score, 0.0, 1.0)), 4)

    def _label_by_score(self, score: float) -> str:
        if score >= self.config.abnormal_threshold:
            return "异常"
        if score >= self.config.suspicious_threshold:
            return "疑似异常"
        return "正常"

    def _abnormal_sources(self, row: pd.Series) -> str:
        sources = []
        if float(row.get("thermal_score", 0.0)) >= 0.5:
            sources.append("温度")
        if float(row.get("vibration_score", 0.0)) >= 0.5:
            sources.append("振动")
        if float(row.get("audio_score", 0.0)) >= 0.5:
            sources.append("音频")
        if float(row.get("video_score", 0.0)) >= 0.5:
            sources.append("视频")
        return "、".join(sources) if sources else "无明显异常来源"


# =============================
# 六、测试数据生成
# =============================

def make_demo_data(data_dir: str | Path) -> None:
    """生成一组可直接运行的测试数据。"""
    data_dir = Path(data_dir)
    ensure_dir(data_dir)

    base = pd.Timestamp("2026-07-06 10:00:01")

    spatial_map = pd.DataFrame([
        {
            "device_id": "Motor_01",
            "thermal_roi": "ROI_1",
            "video_roi": "ROI_1",
            "vibration_sensor": "Vib_01",
            "audio_sensor": "Mic_01",
        },
        {
            "device_id": "Motor_02",
            "thermal_roi": "ROI_2",
            "video_roi": "ROI_2",
            "vibration_sensor": "Vib_02",
            "audio_sensor": "Mic_02",
        },
    ])
    spatial_map.to_csv(data_dir / "spatial_map.csv", index=False, encoding="utf-8-sig")

    thermal = pd.DataFrame([
        {
            "timestamp": base,
            "device_id": "Motor_01",
            "roi_id": "ROI_1",
            "mean_temp": 78.0,
            "max_temp": 92.0,
            "temp_std": 6.5,
            "hot_area_ratio": 0.32,
        },
        {
            "timestamp": base + pd.Timedelta(seconds=3),
            "device_id": "Motor_02",
            "roi_id": "ROI_2",
            "mean_temp": 45.0,
            "max_temp": 57.0,
            "temp_std": 3.1,
            "hot_area_ratio": 0.02,
        },
    ])
    thermal.to_csv(data_dir / "thermal.csv", index=False, encoding="utf-8-sig")

    # 构造 10:00:00 ~ 10:00:02 的振动数据，Motor_01 明显异常
    t = pd.date_range(base - pd.Timedelta(seconds=1), base + pd.Timedelta(seconds=1), periods=80)
    vib_values = 1.6 * np.sin(np.linspace(0, 24 * np.pi, len(t))) + 0.2 * np.random.randn(len(t))
    vibration = pd.DataFrame({
        "timestamp": t,
        "device_id": "Motor_01",
        "sensor_id": "Vib_01",
        "vibration_value": vib_values,
    })
    vibration.to_csv(data_dir / "vibration.csv", index=False, encoding="utf-8-sig")

    audio = pd.DataFrame([
        {
            "timestamp": base,
            "device_id": "Motor_01",
            "sensor_id": "Mic_01",
            "audio_energy": 0.82,
            "zero_crossing_rate": 0.21,
        },
        {
            "timestamp": base + pd.Timedelta(seconds=3),
            "device_id": "Motor_02",
            "sensor_id": "Mic_02",
            "audio_energy": 0.18,
            "zero_crossing_rate": 0.08,
        },
    ])
    audio.to_csv(data_dir / "audio.csv", index=False, encoding="utf-8-sig")

    video = pd.DataFrame([
        {
            "timestamp": base,
            "device_id": "Motor_01",
            "roi_id": "ROI_1",
            "object_state": "异常抖动",
            "motion_score": 0.91,
        },
        {
            "timestamp": base + pd.Timedelta(seconds=3),
            "device_id": "Motor_02",
            "roi_id": "ROI_2",
            "object_state": "正常运行",
            "motion_score": 0.16,
        },
    ])
    video.to_csv(data_dir / "video.csv", index=False, encoding="utf-8-sig")


# =============================
# 七、主流程
# =============================

def run_algorithm(config: FusionConfig) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """执行完整算法流程。"""
    ensure_dir(config.output_dir)

    loader = MultiSourceDataLoader(config)
    thermal, vibration, audio, video, spatial_map = loader.load_all()

    aligner = SpatioTemporalAligner(config)
    aligned_df = aligner.align(thermal, vibration, audio, video, spatial_map)

    fusion = MultiModalFusionDecision(config)
    result_df = fusion.predict(aligned_df)

    aligned_path = Path(config.output_dir) / "aligned_records.csv"
    result_path = Path(config.output_dir) / "fusion_result.csv"
    report_path = Path(config.output_dir) / "fusion_report.json"

    aligned_df.to_csv(aligned_path, index=False, encoding="utf-8-sig")
    result_df.to_csv(result_path, index=False, encoding="utf-8-sig")

    report = {
        "config": asdict(config),
        "input_count": {
            "thermal": int(len(thermal)),
            "vibration": int(len(vibration)),
            "audio": int(len(audio)),
            "video": int(len(video)),
            "spatial_map": int(len(spatial_map)),
        },
        "aligned_records": int(len(aligned_df)),
        "result_count_by_label": result_df["final_result"].value_counts().to_dict() if not result_df.empty else {},
        "output_files": {
            "aligned_records": str(aligned_path),
            "fusion_result": str(result_path),
        },
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=4, default=str)

    return aligned_df, result_df


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="多源异构数据时空对齐与融合算法")
    parser.add_argument("--data_dir", type=str, default="./data", help="数据目录")
    parser.add_argument("--output_dir", type=str, default="./output", help="输出目录")
    parser.add_argument("--time_window", type=float, default=1.0, help="时间对齐窗口，单位秒")
    parser.add_argument("--make_demo", action="store_true", help="生成示例数据并运行")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    config = FusionConfig(
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        time_window_seconds=args.time_window,
    )

    if args.make_demo:
        make_demo_data(config.data_dir)
        print(f"已生成测试数据：{config.data_dir}")

    aligned_df, result_df = run_algorithm(config)

    print("\n========== 多源异构数据时空对齐结果 ==========")
    if aligned_df.empty:
        print("未生成对齐记录，请检查输入数据和空间映射表。")
    else:
        print(aligned_df[["event_time", "device_id", "time_match", "space_match", "alignment_result"]].to_string(index=False))

    print("\n========== 多模态融合判别结果 ==========")
    if result_df.empty:
        print("未生成融合判别结果。")
    else:
        show_cols = [
            "event_time", "device_id", "thermal_score", "vibration_score",
            "audio_score", "video_score", "fusion_score", "final_result",
            "confidence", "abnormal_sources",
        ]
        print(result_df[show_cols].to_string(index=False))

    print(f"\n对齐结果已保存：{Path(config.output_dir) / 'aligned_records.csv'}")
    print(f"融合结果已保存：{Path(config.output_dir) / 'fusion_result.csv'}")
    print(f"运行报告已保存：{Path(config.output_dir) / 'fusion_report.json'}")


if __name__ == "__main__":
    main()
