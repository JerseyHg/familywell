"""
app/services/health_validator.py — 健康指标数值范围校验
─────────────────────────────────────────────────────────
[P0-3] 三层校验：硬规则表（入库前同步）→ AI 标记复核 → 丢弃不合理数据

校验规则基于医学常识，不需要AI，零延迟零成本100%确定性。
"""
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class IndicatorRule:
    """单个指标的校验规则"""
    name: str               # 中文名（用于日志/提示）
    unit: str               # 常见单位
    abs_min: float          # 绝对下限（低于此值物理上不可能）
    abs_max: float          # 绝对上限（高于此值物理上不可能）
    normal_low: float       # 正常参考范围下限
    normal_high: float      # 正常参考范围上限


# ════════════════════════════════════════
# 硬规则表 — 常见健康指标的合理范围
# ════════════════════════════════════════

INDICATOR_RULES: dict[str, IndicatorRule] = {
    # ── 血压 ──
    "systolic_bp": IndicatorRule(
        name="收缩压", unit="mmHg",
        abs_min=50, abs_max=300,
        normal_low=90, normal_high=140,
    ),
    "diastolic_bp": IndicatorRule(
        name="舒张压", unit="mmHg",
        abs_min=30, abs_max=200,
        normal_low=60, normal_high=90,
    ),
    "heart_rate": IndicatorRule(
        name="心率", unit="bpm",
        abs_min=20, abs_max=300,
        normal_low=60, normal_high=100,
    ),

    # ── 血糖 ──
    "fasting_glucose": IndicatorRule(
        name="空腹血糖", unit="mmol/L",
        abs_min=1.0, abs_max=50.0,
        normal_low=3.9, normal_high=6.1,
    ),
    "glucose": IndicatorRule(
        name="血糖", unit="mmol/L",
        abs_min=1.0, abs_max=50.0,
        normal_low=3.9, normal_high=11.1,
    ),
    "hba1c": IndicatorRule(
        name="糖化血红蛋白", unit="%",
        abs_min=2.0, abs_max=20.0,
        normal_low=4.0, normal_high=6.5,
    ),

    # ── 血脂 ──
    "total_cholesterol": IndicatorRule(
        name="总胆固醇", unit="mmol/L",
        abs_min=1.0, abs_max=20.0,
        normal_low=2.8, normal_high=5.2,
    ),
    "ldl": IndicatorRule(
        name="低密度脂蛋白", unit="mmol/L",
        abs_min=0.1, abs_max=15.0,
        normal_low=0.0, normal_high=3.4,
    ),
    "hdl": IndicatorRule(
        name="高密度脂蛋白", unit="mmol/L",
        abs_min=0.1, abs_max=10.0,
        normal_low=1.0, normal_high=3.0,
    ),
    "triglycerides": IndicatorRule(
        name="甘油三酯", unit="mmol/L",
        abs_min=0.1, abs_max=30.0,
        normal_low=0.0, normal_high=1.7,
    ),

    # ── 肝功 ──
    "alt": IndicatorRule(
        name="谷丙转氨酶", unit="U/L",
        abs_min=0, abs_max=5000,
        normal_low=0, normal_high=40,
    ),
    "ast": IndicatorRule(
        name="谷草转氨酶", unit="U/L",
        abs_min=0, abs_max=5000,
        normal_low=0, normal_high=40,
    ),

    # ── 肾功 ──
    "creatinine": IndicatorRule(
        name="肌酐", unit="μmol/L",
        abs_min=10, abs_max=2000,
        normal_low=44, normal_high=133,
    ),
    "bun": IndicatorRule(
        name="尿素氮", unit="mmol/L",
        abs_min=0.5, abs_max=80,
        normal_low=2.9, normal_high=8.2,
    ),
    "uric_acid": IndicatorRule(
        name="尿酸", unit="μmol/L",
        abs_min=50, abs_max=1500,
        normal_low=150, normal_high=420,
    ),

    # ── 血常规 ──
    "wbc": IndicatorRule(
        name="白细胞", unit="×10⁹/L",
        abs_min=0.1, abs_max=500,
        normal_low=4.0, normal_high=10.0,
    ),
    "rbc": IndicatorRule(
        name="红细胞", unit="×10¹²/L",
        abs_min=0.5, abs_max=10.0,
        normal_low=3.5, normal_high=5.5,
    ),
    "hemoglobin": IndicatorRule(
        name="血红蛋白", unit="g/L",
        abs_min=20, abs_max=250,
        normal_low=110, normal_high=160,
    ),
    "platelets": IndicatorRule(
        name="血小板", unit="×10⁹/L",
        abs_min=5, abs_max=1500,
        normal_low=100, normal_high=300,
    ),

    # ── 甲状腺 ──
    "tsh": IndicatorRule(
        name="促甲状腺素", unit="mIU/L",
        abs_min=0.001, abs_max=200,
        normal_low=0.35, normal_high=5.5,
    ),
    "ft3": IndicatorRule(
        name="游离T3", unit="pmol/L",
        abs_min=0.5, abs_max=30,
        normal_low=3.1, normal_high=6.8,
    ),
    "ft4": IndicatorRule(
        name="游离T4", unit="pmol/L",
        abs_min=1, abs_max=80,
        normal_low=12, normal_high=22,
    ),

    # ── 肿瘤标志物 ──
    "psa": IndicatorRule(
        name="PSA", unit="ng/mL",
        abs_min=0, abs_max=10000,
        normal_low=0, normal_high=4.0,
    ),
    "cea": IndicatorRule(
        name="癌胚抗原", unit="ng/mL",
        abs_min=0, abs_max=5000,
        normal_low=0, normal_high=5.0,
    ),
    "afp": IndicatorRule(
        name="甲胎蛋白", unit="ng/mL",
        abs_min=0, abs_max=50000,
        normal_low=0, normal_high=20,
    ),

    # ── 体征 ──
    "body_temperature": IndicatorRule(
        name="体温", unit="°C",
        abs_min=30, abs_max=45,
        normal_low=36.0, normal_high=37.3,
    ),
    "bmi": IndicatorRule(
        name="BMI", unit="kg/m²",
        abs_min=8, abs_max=80,
        normal_low=18.5, normal_high=24.9,
    ),
    "weight": IndicatorRule(
        name="体重", unit="kg",
        abs_min=2, abs_max=300,
        normal_low=40, normal_high=100,
    ),
    "height": IndicatorRule(
        name="身高", unit="cm",
        abs_min=40, abs_max=250,
        normal_low=150, normal_high=190,
    ),
    "spo2": IndicatorRule(
        name="血氧饱和度", unit="%",
        abs_min=30, abs_max=100,
        normal_low=95, normal_high=100,
    ),
}

# 同义词/别名映射 → 标准 key
_TYPE_ALIASES: dict[str, str] = {
    "收缩压": "systolic_bp",
    "高压": "systolic_bp",
    "sbp": "systolic_bp",
    "舒张压": "diastolic_bp",
    "低压": "diastolic_bp",
    "dbp": "diastolic_bp",
    "心率": "heart_rate",
    "脉搏": "heart_rate",
    "hr": "heart_rate",
    "pulse": "heart_rate",
    "空腹血糖": "fasting_glucose",
    "fpg": "fasting_glucose",
    "血糖": "glucose",
    "glu": "glucose",
    "糖化血红蛋白": "hba1c",
    "糖化": "hba1c",
    "总胆固醇": "total_cholesterol",
    "tc": "total_cholesterol",
    "低密度脂蛋白": "ldl",
    "ldl-c": "ldl",
    "高密度脂蛋白": "hdl",
    "hdl-c": "hdl",
    "甘油三酯": "triglycerides",
    "tg": "triglycerides",
    "谷丙转氨酶": "alt",
    "丙氨酸氨基转移酶": "alt",
    "谷草转氨酶": "ast",
    "天冬氨酸氨基转移酶": "ast",
    "肌酐": "creatinine",
    "cr": "creatinine",
    "crea": "creatinine",
    "尿素氮": "bun",
    "尿素": "bun",
    "尿酸": "uric_acid",
    "ua": "uric_acid",
    "白细胞": "wbc",
    "红细胞": "rbc",
    "血红蛋白": "hemoglobin",
    "hb": "hemoglobin",
    "hgb": "hemoglobin",
    "血小板": "platelets",
    "plt": "platelets",
    "促甲状腺素": "tsh",
    "体温": "body_temperature",
    "temp": "body_temperature",
    "血氧": "spo2",
    "血氧饱和度": "spo2",
}


@dataclass
class ValidationResult:
    """校验结果"""
    is_valid: bool              # 值是否在物理可能范围内
    is_abnormal: bool | None    # 是否异常（校验通过但超出正常范围）
    original_value: float
    corrected_value: float | None  # 如果需要修正（暂时为 None，未来可扩展）
    message: str | None         # 校验不通过的原因


def normalize_indicator_type(raw_type: str) -> str:
    """
    将 AI 返回的指标类型标准化。
    先查别名表，再尝试转小写匹配规则表。
    """
    raw_lower = raw_type.lower().strip()

    # 1. 别名表精确匹配
    if raw_type in _TYPE_ALIASES:
        return _TYPE_ALIASES[raw_type]
    if raw_lower in _TYPE_ALIASES:
        return _TYPE_ALIASES[raw_lower]

    # 2. 直接匹配规则表 key
    if raw_lower in INDICATOR_RULES:
        return raw_lower

    # 3. 替换常见分隔符
    normalized = raw_lower.replace("-", "_").replace(" ", "_")
    if normalized in INDICATOR_RULES:
        return normalized

    return raw_type  # 无法识别，保留原值


def validate_indicator(indicator_type: str, value: float) -> ValidationResult:
    """
    校验单个指标值。

    返回:
      - is_valid=False: 值物理上不可能，应丢弃
      - is_valid=True, is_abnormal=True: 值可能但超出正常范围
      - is_valid=True, is_abnormal=False: 值在正常范围内
      - is_valid=True, is_abnormal=None: 不在规则表中，无法判断
    """
    std_type = normalize_indicator_type(indicator_type)
    rule = INDICATOR_RULES.get(std_type)

    if not rule:
        # 不在规则表中 → 信任 AI 的判断
        return ValidationResult(
            is_valid=True,
            is_abnormal=None,
            original_value=value,
            corrected_value=None,
            message=None,
        )

    # 第一层：绝对范围校验
    if value < rule.abs_min or value > rule.abs_max:
        logger.warning(
            f"指标 {rule.name}({indicator_type}) 值 {value} 超出物理范围 "
            f"[{rule.abs_min}, {rule.abs_max}]，已丢弃"
        )
        return ValidationResult(
            is_valid=False,
            is_abnormal=None,
            original_value=value,
            corrected_value=None,
            message=f"{rule.name} 值 {value} {rule.unit} 不在合理范围内（{rule.abs_min}-{rule.abs_max}），可能识别有误",
        )

    # 第二层：正常范围判断
    is_abnormal = value < rule.normal_low or value > rule.normal_high

    return ValidationResult(
        is_valid=True,
        is_abnormal=is_abnormal,
        original_value=value,
        corrected_value=None,
        message=None,
    )


def validate_indicators_batch(indicators: list[dict]) -> tuple[list[dict], list[str]]:
    """
    批量校验一组指标。

    参数:
        indicators: AI 识别出的指标列表，每个是 {"type": str, "value": float, ...}

    返回:
        (valid_indicators, warnings)
        valid_indicators: 校验通过的指标（可能修正了 abnormal 标记）
        warnings: 被丢弃或修正的警告信息
    """
    valid = []
    warnings = []

    for ind in indicators:
        raw_type = ind.get("type", "unknown")
        try:
            value = float(ind.get("value", 0))
        except (TypeError, ValueError):
            warnings.append(f"指标 {raw_type} 的值 '{ind.get('value')}' 不是有效数字，已跳过")
            continue

        result = validate_indicator(raw_type, value)

        if not result.is_valid:
            warnings.append(result.message or f"{raw_type} 值异常，已丢弃")
            continue

        # 标准化指标类型
        ind["type"] = normalize_indicator_type(raw_type)

        # 如果规则表能判断异常，以规则表为准（覆盖 AI 的 abnormal 标记）
        if result.is_abnormal is not None:
            ai_abnormal = ind.get("abnormal", False)
            if ai_abnormal != result.is_abnormal:
                logger.info(
                    f"指标 {raw_type} 异常标记已修正: AI={ai_abnormal} → 规则={result.is_abnormal}"
                )
            ind["abnormal"] = result.is_abnormal

        valid.append(ind)

    return valid, warnings
