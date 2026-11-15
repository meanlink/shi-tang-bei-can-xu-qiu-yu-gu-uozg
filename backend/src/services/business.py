"""星期周期的加法Holt-Winters预测、时间留出回测与备餐缓冲。"""
from datetime import date, timedelta
import math
import numpy as np

PERIOD = 7


def metadata():
    return {
        "name": "食堂备餐需求预估", "short": "meal-seasonal",
        "tagline": "保留星期节律、先回测再出备餐建议，把缓冲量单独列明。",
        "client": "东港研发园 · 3人食堂计划组 · 单一午餐档112天实际领餐量",
        "workflow": "计划员按上周同日复制数量，再凭经验加餐，事后用剩餐记录复盘。",
        "pains": ["简单照搬上周忽略缓慢增长趋势", "模型未在未来时段回测", "预测误差与人为安全缓冲混在一起"],
        "upstream": "每日午餐实际核销份数 lunch_actual_20260720_20261108.csv",
        "downstream": "计划组审核未来7~21天备餐份数，采购与厨房据审核结果安排批次",
        "entities": ["每日领餐观测", "周期预测试验", "备餐审核简报"],
        "dataset_name": "东港园午餐112天实际领餐记录",
        "columns": [{"key": "date", "label": "领餐日期", "type": "string"}, {"key": "demand", "label": "实际领餐份数", "type": "number"}, {"key": "meal", "label": "档口", "type": "string"}],
        "parameters": [{"key": "alpha", "label": "水平平滑系数α", "type": "number", "default": 0.35, "min": 0.1, "max": 0.9}, {"key": "horizon", "label": "预测天数", "type": "number", "default": 7, "min": 7, "max": 21}, {"key": "safety_pct", "label": "计划安全缓冲(%)", "type": "number", "default": 8, "min": 0, "max": 20}],
        "workbench_title": "星期周期与备餐缓冲试算", "action_label": "回测并生成备餐建议", "unit": "日观测",
        "accent": "oklch(58% 0.12 80)", "style": "kitchen-sheet-archive-press",
        "seed_story": ["06:30 计划员导入 lunch_actual_20260720_20261108.csv 的112天记录", "06:40 校验每天一条且连续，午餐档口一致", "06:50 以α=0.35、预测7天和8%缓冲创建试验", "06:51 后端留出最后14天，按时间顺序回测", "07:00 计划员比较Holt-Winters与上周同日基线的WAPE", "07:10 查看未来每日预测、缓冲份数和最终建议", "07:20 将缓冲改为12%比较备餐占用", "07:30 下载建议表，审核后交厨房批次计划", "16:30 管理员设每日16:30内部简报归档"],
        "method": "周期7天的加法Holt-Winters；α可调，趋势β=0.15、季节γ=0.20。最后14天作时间留出，报告MAE和WAPE并对照上周同日季节朴素法；全部历史重拟合后预测未来，建议份数=ceil(预测×(1+缓冲比例))。",
        "limitations": ["未接入节假日、会议或园区停工信息", "安全缓冲是计划参数，不是统计置信区间", "仅适用于单一档口的连续每日数据"],
    }


def seed_records():
    rng = np.random.default_rng(1014)
    start = date(2026, 7, 20)
    weekday = [480, 520, 550, 530, 470, 180, 140]
    rows = []
    for i in range(112):
        current = start + timedelta(days=i)
        demand = max(0, int(round(weekday[current.weekday()] + 0.45 * i + rng.normal(0, 13))))
        rows.append({"date": current.isoformat(), "demand": demand, "meal": "东港园午餐"})
    return rows


def _forecast(values, horizon, alpha):
    """初始化只使用训练窗口前两个周期，绝不读取验证或未来值。"""
    values = np.asarray(values, dtype=float)
    level = float(np.mean(values[:PERIOD]))
    trend = float((np.mean(values[PERIOD:2 * PERIOD]) - level) / PERIOD)
    seasons = list(values[:PERIOD] - level)
    beta, gamma = 0.15, 0.20
    for index in range(PERIOD, len(values)):
        slot = index % PERIOD
        previous_level = level
        previous_season = seasons[slot]
        level = alpha * (float(values[index]) - previous_season) + (1 - alpha) * (level + trend)
        trend = beta * (level - previous_level) + (1 - beta) * trend
        seasons[slot] = gamma * (float(values[index]) - level) + (1 - gamma) * previous_season
    return np.asarray([max(0.0, level + step * trend + seasons[(len(values) + step - 1) % PERIOD]) for step in range(1, horizon + 1)])


def analyze(records, params):
    try:
        alpha = float(params.get("alpha", 0.35))
        raw_horizon = float(params.get("horizon", 7))
        horizon = int(raw_horizon)
        safety = float(params.get("safety_pct", 8))
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("平滑系数、天数和安全缓冲须为有效数值。") from exc
    if not 0.1 <= alpha <= 0.9 or raw_horizon != horizon or not 7 <= horizon <= 21 or not 0 <= safety <= 20:
        raise ValueError("α须0.1~0.9，预测天数须7~21整数，缓冲须0~20%。")
    if len(records) < 49:
        raise ValueError("至少需要连续49天记录，以保留14天独立回测。")
    parsed = []
    meals = set()
    for row in records:
        try:
            day = date.fromisoformat(str(row["date"]))
            value = float(row["demand"])
            meal = str(row["meal"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("日期须YYYY-MM-DD，份数须非负数，档口须完整。") from exc
        if not np.isfinite(value) or value < 0 or not meal:
            raise ValueError("领餐份数须非负有限，档口不能为空。")
        parsed.append((day, value))
        meals.add(meal)
    if len(meals) != 1:
        raise ValueError("一次仅分析一个档口，请先按档口拆分数据。")
    parsed.sort(key=lambda pair: pair[0])
    if any((parsed[i][0] - parsed[i-1][0]).days != 1 for i in range(1, len(parsed))):
        raise ValueError("每日须恰好一条连续记录；停餐日请显式补0，不可跳日或重复。")
    values = np.asarray([pair[1] for pair in parsed])
    train, actual = values[:-14], values[-14:]
    if float(actual.sum()) <= 0:
        raise ValueError("最后14天实际领餐量总和须大于0，否则WAPE无定义。")
    predicted = _forecast(train, 14, alpha)
    naive = np.tile(train[-PERIOD:], 2)
    mae = float(np.mean(np.abs(actual - predicted)))
    wape = float(np.sum(np.abs(actual - predicted)) / np.sum(actual) * 100)
    naive_wape = float(np.sum(np.abs(actual - naive)) / np.sum(actual) * 100)
    future = _forecast(values, horizon, alpha)
    rows = []
    for i, value in enumerate(future, 1):
        day = parsed[-1][0] + timedelta(days=i)
        planned = int(math.ceil(float(value) * (1 + safety / 100) - 1e-9))
        rows.append({"date": day.isoformat(), "weekday": "周" + "一二三四五六日"[day.weekday()], "forecast": round(float(value), 1), "buffer": round(planned - float(value), 1), "plan": planned})
    return {
        "summary": f"以{len(values)-14}天训练、最后14天回测，WAPE为{wape:.2f}%；基于全部{len(values)}天预测未来{horizon}天，并单列{safety:g}%计划缓冲。",
        "metrics": [{"label": "回测MAE", "value": round(mae, 2), "unit": "份/天"}, {"label": "回测WAPE", "value": round(wape, 2), "unit": "%"}, {"label": "上周同日基线WAPE", "value": round(naive_wape, 2), "unit": "%"}, {"label": "预测期建议总量", "value": sum(row["plan"] for row in rows), "unit": "份"}],
        "table": {"columns": [{"key": "date", "label": "预测日期"}, {"key": "weekday", "label": "星期"}, {"key": "forecast", "label": "基础预测(份)"}, {"key": "buffer", "label": "额外缓冲(份)"}, {"key": "plan", "label": "建议备餐(份)"}], "rows": rows},
        "chart": {"type": "line", "labels": [row["date"] for row in rows], "series": [{"name": "基础预测", "values": [row["forecast"] for row in rows]}, {"name": "含缓冲备餐建议", "values": [row["plan"] for row in rows]}]},
        "findings": [f"回测相对上周同日基线的WAPE变化为{wape-naive_wape:+.2f}个百分点（负值表示改善）。", f"未来{horizon}天基础预测合计{float(future.sum()):.1f}份，建议合计{sum(row['plan'] for row in rows)}份。", "先审核特殊活动和休园日，再确认备餐批次；缓冲不改变模型回测指标。"],
        "method": metadata()["method"], "warnings": ["节假日与活动尚未作为外生变量；缓冲不是置信区间。"] + (["当前模型未优于上周同日基线，应优先复核趋势与异常日。"] if wape >= naive_wape else []),
    }
