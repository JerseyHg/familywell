import json
import base64
import logging
from openai import AsyncOpenAI
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_client = AsyncOpenAI(
    api_key=settings.DOUBAO_API_KEY,
    base_url=settings.DOUBAO_BASE_URL,
)

# ════════════════════════════════════════
# ✅ 重写识别 Prompt：结构化提取 + 全文 OCR
# ════════════════════════════════════════

RECOGNITION_PROMPT = """你是一个医疗文档识别助手。请分析这张图片，完成两件事：
1. 判断类别并提取结构化信息
2. **完整逐字抄录图片上的所有可见文字**（包括表头、检查所见、诊断结论、医嘱、备注、页眉页脚等一切内容）

请仅返回JSON格式（不要包含```json标记），必须包含以下字段：
{
    "category": "checkup|lab|prescription|insurance|visit|food|bp_reading|weight|other",
    "title": "文档标题",
    "confidence": 0.95,

    // ★★★ 最重要的字段 ★★★
    // 把图片上能看到的所有文字，原样逐字抄录到这里
    // 包括但不限于：报告正文、检查所见、影像描述、诊断意见、
    // 病史摘要、医嘱、备注、表格内容、页眉页脚等
    // 不要省略、不要概括，完整抄录
    "raw_text": "图片上所有可见文字的完整抄录...",

    // 以下根据 category 不同填写对应字段：

    // checkup / lab:
    "hospital": "医院名称",
    "date": "YYYY-MM-DD",
    "department": "科室",
    "doctor": "医生",
    "diagnosis": "诊断结论（原文）",
    "findings": "检查所见/影像描述（原文）",
    "recommendations": "建议/医嘱（原文）",
    "indicators": [
        { "name": "指标中文名", "type": "psa", "value": 0.8, "unit": "ng/mL",
          "abnormal": false, "reference_low": 0, "reference_high": 4.0 }
    ],

    // visit（就诊记录 / MR诊断报告 / 出院小结等）:
    "hospital": "医院名称",
    "department": "科室",
    "doctor": "医生",
    "date": "YYYY-MM-DD",
    "chief_complaint": "主诉（原文）",
    "present_illness": "现病史（原文）",
    "past_history": "既往史（原文）",
    "physical_exam": "体格检查（原文）",
    "findings": "检查所见/影像描述（原文）",
    "diagnosis": "诊断结论（原文）",
    "recommendations": "治疗方案/建议/医嘱（原文）",

    // prescription:
    "hospital": "...",
    "doctor": "...",
    "date": "YYYY-MM-DD",
    "diagnosis": "诊断",
    "medications": [
        { "name": "药品名", "dosage": "5mg", "frequency": "每日1次",
          "times": ["08:00"], "quantity": 30 }
    ],

    // insurance:
    "provider": "保险公司",
    "policy_type": "百万医疗险",
    "policy_number": "...",
    "insured_name": "被保人",
    "start_date": "YYYY-MM-DD",
    "end_date": "YYYY-MM-DD",
    "premium": 1500.00,
    "coverage": 3000000.00,

    // food:
    "meal_type": "breakfast|lunch|dinner|snack",
    "food_items": [{"name": "米饭", "amount": "200g"}],
    "calories": 650,
    "protein_g": 25,
    "fat_g": 18,
    "carb_g": 85,
    "fiber_g": 5,
    "sodium_mg": 800,

    // bp_reading:
    "systolic": 125,
    "diastolic": 77,
    "heart_rate": 72,
    "date": "YYYY-MM-DD"
}

注意：
1. raw_text 是最关键的字段 —— 把图片上能看见的每一个字都抄进去，越完整越好
2. 对于检查报告、MR/CT报告、病历等，findings（检查所见）和 diagnosis（诊断结论）必须原文抄录，不要概括
3. 如果无法确定某个字段，设为null
4. 日期统一用YYYY-MM-DD格式
5. 数值类型不要带单位
6. 异常指标需要标记abnormal=true
7. 只返回JSON，不要多余文字"""

VOICE_PARSE_PROMPTS = {
    "basic_info": "从以下文字中提取个人基本信息，返回JSON：{\"real_name\": str|null, \"gender\": \"male\"|\"female\"|null, \"age\": int|null}。原文：",
    "blood_type": "从以下文字中提取血型信息，返回JSON：{\"blood_type\": \"A\"|\"B\"|\"AB\"|\"O\"|null}。原文：",
    "allergies": "从以下文字中提取过敏信息，返回JSON：{\"allergies\": [str]}。如果没有过敏就返回空数组。原文：",
    "medical_history": "从以下文字中提取既往病史，返回JSON：{\"medical_history\": [str]}。如果没有疾病就返回空数组。原文：",
    "emergency_contact": "从以下文字中提取紧急联系人信息，返回JSON：{\"emergency_contact_name\": str|null, \"emergency_contact_phone\": str|null}。原文：",
}


async def recognize_image(image_base64: str) -> dict:
    """Send image to Doubao for recognition and structured extraction."""
    try:
        response = await _client.chat.completions.create(
            model=settings.DOUBAO_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": RECOGNITION_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_base64}"
                            },
                        },
                    ],
                }
            ],
            # ✅ 提高到 4096，确保长报告的 raw_text 不被截断
            max_tokens=4096,
            temperature=0.1,
        )

        text = response.choices[0].message.content.strip()
        # Clean up potential markdown code fences
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"AI returned invalid JSON: {text[:200]}... Error: {e}")
        return {"category": "other", "title": "识别失败", "error": str(e)}
    except Exception as e:
        logger.error(f"AI recognition failed: {e}")
        raise


async def parse_voice_text(step: str, text: str) -> dict:
    """Parse voice-to-text result for onboarding steps."""
    prompt = VOICE_PARSE_PROMPTS.get(step, "")
    if not prompt:
        return {"error": f"Unknown step: {step}"}

    try:
        response = await _client.chat.completions.create(
            model=settings.DOUBAO_MODEL,
            messages=[
                {"role": "user", "content": f"{prompt}{text}"}
            ],
            max_tokens=500,
            temperature=0.1,
        )

        result_text = response.choices[0].message.content.strip()
        if result_text.startswith("```"):
            result_text = result_text.split("\n", 1)[1]
        if result_text.endswith("```"):
            result_text = result_text.rsplit("```", 1)[0]

        return json.loads(result_text.strip())
    except Exception as e:
        logger.error(f"Voice parse failed for step {step}: {e}")
        return {"error": str(e)}


async def generate_health_tip(indicators_summary: str) -> str:
    """Generate daily AI health tip based on recent indicators."""
    try:
        response = await _client.chat.completions.create(
            model=settings.DOUBAO_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "你是一个家庭健康助手，请根据用户近期的健康数据给出简短的健康提示。语气温暖友好，50-80字即可。",
                },
                {"role": "user", "content": indicators_summary},
            ],
            max_tokens=200,
            temperature=0.7,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Health tip generation failed: {e}")
        return ""
