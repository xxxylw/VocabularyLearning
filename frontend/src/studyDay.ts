// 学习日锚点（2026-09 规格：每日学习刷新时间改为凌晨 2:00）。
// 学习日 D = 北京时间（固定 UTC+8，无夏令时）自然日 D，边界凌晨
// 02:00 —— [D 日 02:00, D+1 日 02:00) 属于学习日 D。
// 协调员拍板 D1：固定 Asia/Shanghai，不跟随用户浏览器本地时区；
// 与后端 app/study_clock.py 保持同一口径。

export const STUDY_TZ_OFFSET_MINUTES = 8 * 60;
export const STUDY_DAY_START_HOUR = 2;

function beijingWallClock(now: Date): Date {
  // 把北京墙上时刻装进 UTC 字段（+8h 精确换算，无 DST），
  // 与浏览器本地时区无关。
  return new Date(now.getTime() + STUDY_TZ_OFFSET_MINUTES * 60_000);
}

function studyDayUtcFields(now: Date): Date {
  const wall = beijingWallClock(now);
  if (wall.getUTCHours() < STUDY_DAY_START_HOUR) {
    wall.setUTCDate(wall.getUTCDate() - 1);
  }
  return wall;
}

// 当前时刻所属「学习日」的锚点 Date：一个本地午夜 Date，其日历字段
// （getFullYear/getMonth/getDate）就是学习日的自然日。下游纯函数
// （estimate 预估窗口、CheckInGrid 13 周网格、localDateString）都
// 通过本地 getter 取日期 —— 传入锚点即可整体跟随学习日口径。
export function currentStudyDayAnchor(now: Date = new Date()): Date {
  const day = studyDayUtcFields(now);
  return new Date(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate());
}

// 学习日日期键（YYYY-MM-DD）。打卡记录 date 字段用它归日：
// 北京 00:00–02:00 之间完成的队列记到前一自然日（即当前学习日）。
export function studyDayKey(now: Date = new Date()): string {
  const day = studyDayUtcFields(now);
  return day.toISOString().slice(0, 10);
}
