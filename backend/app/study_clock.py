"""学习日口径（PM 规格：每日学习刷新时间改为凌晨 2:00）。

学习日 = [02:00, 次日 02:00)，以起始自然日命名（09-11 02:00 至
09-12 02:00 为学习日 09-11）。唯一口径函数::

    study_day(t) = (t - 2 小时) 在固定 Asia/Shanghai 时区的自然日

规格决策（协调员拍板，均按 PM 推荐）：
- D1 时区：固定 Asia/Shanghai（+08:00，无夏令时），不跟随用户本地
  时区，也不跟随服务器本地时区 —— 由此前的 ``astimezone().date()``
  （服务器本地时区）统一切换为固定时区，保证任何部署环境下口径一致。
- D2 历史数据：不迁移不重算，02:00 口径仅对新数据生效；
  ``reviews_study_date_migration`` 的历史回填语义保持不动。
- D3 归日双轨：今日队列内的卡按队列快照 ``today_queue.study_date``
  归日（允许 current-1 窗口，覆盖「进行中会话跨 02:00 提交」）；
  队列外按提交时刻 ``study_day(t)`` 归日；一律以服务器时间为准，
  客户端 ``reviewedDate`` / ``reviewedAt`` 不再参与归日判定。

测试通过 ``monkeypatch.setattr(study_clock, "now", ...)`` 注入固定
时钟，保证 01:59:59 / 02:00:00 / 02:00:01 三时刻用例的确定性，
不受 CI 机器时区与运行时刻影响。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

# D1：固定 Asia/Shanghai。用固定 UTC 偏移而非 zoneinfo，避免依赖宿主
# 时区数据库，也避免生产容器本地时区漂移影响归日。
STUDY_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")

# 学习日边界：凌晨 2:00（含端点，02:00:00.000 起属新学习日）。
STUDY_DAY_START_HOUR = 2


def now() -> datetime:
    """当前时刻（aware UTC）。测试 monkeypatch 本函数注入固定时钟。"""
    return datetime.now(timezone.utc)


def study_day(moment: datetime) -> date:
    """学习日口径函数：moment 所在学习日 = (moment - 2h) 在固定
    Asia/Shanghai 时区的自然日。naive 时刻按 UTC 解释。"""
    normalized = moment
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=timezone.utc)
    shifted = normalized.astimezone(STUDY_TZ) - timedelta(hours=STUDY_DAY_START_HOUR)
    return shifted.date()


def current_study_day() -> date:
    """服务端当前学习日（00:00-02:00 窗口内返回前一自然日对应的学习日）。"""
    return study_day(now())
