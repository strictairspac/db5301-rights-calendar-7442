export const topic = {
  "name": "潮幕影展·放映授权排期",
  "direction": "窗口 SQL 与时段聚合",
  "modules": [
    "影片台账",
    "授权窗口",
    "排片校核",
    "缺口单"
  ],
  "algorithm": "用 PostgreSQL SQL 合并相接授权窗口，输出每场未授权分钟和权益缺口"
};
