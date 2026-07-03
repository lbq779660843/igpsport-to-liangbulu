---
name: igpsport-to-liangbulu
description: Use when converting GPX tracks exported from iGPSPORT/Garmin devices for import into 两步路(Liangbulu) or similar sports apps — symptoms include mismatched moving time, distance, or cumulative ascent after import (e.g. file shows 752m ascent but app displays 125m). Also use when generating cycling route reports (road book) from GPX tracks with segment-by-segment elevation, grade, road type analysis, and multi-day itinerary planning.
---

# iGPSPORT 轨迹转两步路

## 概述

将 iGPSPORT/Garmin 等设备导出的 GPX 轨迹修正为在两步路等运动 APP 中显示正确的运动时长、距离和累计爬升。包含两个工具：

1. **gpx_calibrate.py** — 海拔反推校准 + 时间压缩，解决"文件 752m 爬升 → APP 显示 125m"的问题
2. **gpx_route_analyze.py** — 骑行路线分析，自动分段并生成路书（路况评估、补给建议、风险提示、行程规划）

**核心原理**：两步路等 APP 对 GPX 海拔做内部滤波（滑动平均+阈值过滤），导致爬升缩水。本工具用已知校准点（不同 scale 下 APP 显示的爬升值）反推 APP 的滤波参数，然后二分搜索精确的 scale 值，使 APP 输出 = 目标爬升。

## 何时使用

**适用场景：**
- iGPSPORT/Garmin 导出 GPX 后，导入两步路显示的运动时长/累计爬升偏差很大
- 两步路用总经过时间当运动耗时（不剔除停止段），且对海拔做内部滤波导致爬升缩水
- 需要根据 GPX 轨迹生成骑行路书（含分段路况、行程规划、安全建议）

**不适用：**
- 两步路完全忽略 GPX 中的 `<ele>` 标签（用自己 DEM 计算海拔）— 此方法无效，需换用读取文件海拔的 APP
- 单纯的 GPX 格式转换（用 gpsbabel 等工具即可）

## 工作流程

### 第一步：收集参数

向用户询问：

1. **输入 GPX 文件路径** — 原始轨迹文件
2. **目标运动时长** — 真实运动时间（来自设备 APP），格式 `HH:MM:SS`
3. **目标骑行距离** — 真实骑行距离（km）
4. **目标累计爬升** — 真实累计爬升（m）
5. **校准数据** — 至少 2 组 `(scale 值, 两步路显示的爬升 m)`

**如果用户没有校准数据**，按以下方法获取：
- 先用 `--calib 1.0,0` 生成 scale=1.0 的文件，导入两步路看爬升值，作为第一个校准点
- 再用 `--calib 2.0,0` 生成 scale=2.0 的文件，导入两步路看爬升值，作为第二个校准点

### 第二步：运行海拔校准

脚本位于 `scripts/gpx_calibrate.py`（相对于本 skill 目录）。

```bash
python -u scripts/gpx_calibrate.py \
  -i <输入文件.gpx> \
  -o <输出文件.gpx> \
  --moving-time <HH:MM:SS> \
  --distance <距离km> \
  --ascent <爬升m> \
  --calib <scale1>,<APP显示爬升1> \
  --calib <scale2>,<APP显示爬升2> \
  --name <轨迹名称>
```

**示例：**

```bash
python -u scripts/gpx_calibrate.py \
  -i 1.gpx -o wh_cs_calibrated.gpx \
  --moving-time 12:18:21 --distance 310.77 --ascent 752 \
  --calib 1.0,125 --calib 2.5,860 \
  --name "武汉-长沙骑行"
```

### 第三步：迭代校准（可选）

如果首次校准结果在 APP 中显示的爬升仍有偏差：

1. 将实际显示的爬升值作为新校准点添加
2. 重新运行，校准点越多越精确

```bash
python -u scripts/gpx_calibrate.py \
  -i 1.gpx -o wh_cs_calibrated.gpx \
  --moving-time 12:18:21 --distance 310.77 --ascent 752 \
  --calib 1.0,125 --calib 2.5,860 --calib 2.43,764 \
  --name "武汉-长沙骑行"
```

### 第四步：生成骑行路书（可选）

使用 `scripts/gpx_route_analyze.py` 生成路书：

```bash
python -u scripts/gpx_route_analyze.py \
  -i <输入文件.gpx> \
  -o <路书输出.md> \
  --name <路线名称> \
  --day-km <每日骑行距离km> \
  --segment-km <分段距离km>
```

**示例：**

```bash
python -u scripts/gpx_route_analyze.py \
  -i 1.gpx -o route_report.md \
  --name "武汉-长沙骑行" --day-km 220
```

输出包含：
- 全程概览（总距离、总爬升、海拔范围）
- 分段路况总表（道路类型、爬升、评分）
- 分段详情（坡度分布、起终点坐标）
- 行程规划（按日骑行安排、难度评估、骑行建议）
- 安全与装备建议（必备装备、安全注意、补给建议、避峰建议）

## 核心算法说明

### 时间压缩
检测停止段（速度 < 2km/h），剔除后缩放运动段使总时长 = 目标运动时长。

### 海拔反推校准
1. 网格搜索 APP 滤波参数：threshold (0.2~30.0)、smooth_window (3~51)、pre_smooth_window (3~11)
2. 用校准点计算每组参数的拟合误差（RMSE）
3. 选出最佳参数后，二分搜索最优 scale 值（60 次迭代，精度 1m）

### 差分增益
仅放大有效海拔变化（> 0.15m 的起伏），微小变化不变，防止噪声爆炸。

## 参数文件

每次运行会在输出文件同目录生成 `_params.json`，记录校准参数供后续迭代使用：

```json
{
  "input_file": "1.gpx",
  "target_moving_time": "12:18:21",
  "target_distance_km": 310.77,
  "target_ascent_m": 752,
  "calibrations": [[1.0, 125], [2.5, 860]],
  "optimal_scale": 2.4345,
  "predicted_app_ascent": 752.3,
  "simulator_params": {
    "threshold": 4.2,
    "smooth_window": 11,
    "pre_smooth_window": 5
  }
}
```

## 命令行参数速查

### gpx_calibrate.py

| 参数 | 必填 | 说明 |
|------|------|------|
| `-i / --input` | 是 | 输入 GPX 文件 |
| `-o / --output` | 否 | 输出 GPX 文件（默认 calibrated.gpx）|
| `--moving-time` | 是 | 目标运动时长 HH:MM:SS |
| `--distance` | 是 | 目标骑行距离 km |
| `--ascent` | 是 | 目标累计爬升 m |
| `--calib` | 是 | 校准点 `scale,ascent`，可多次指定 |
| `--name` | 否 | 轨迹名称（默认 Track）|
| `--speed-threshold` | 否 | 运动速度阈值 km/h（默认 2.0）|

### gpx_route_analyze.py

| 参数 | 必填 | 说明 |
|------|------|------|
| `-i / --input` | 是 | 输入 GPX 文件 |
| `-o / --output` | 否 | 输出路书文件（默认 route_report.md）|
| `--name` | 否 | 路线名称（默认 骑行路线）|
| `--segment-km` | 否 | 分段距离 km（默认 60）|
| `--day-km` | 否 | 每日骑行距离 km（默认 180，用于行程规划）|

## 注意事项

- 校准数据是针对**特定 APP** 的，换一个 APP 需要重新校准
- 不同轨迹的校准参数可能不同（地形不同 APP 平滑效果不同），长距离轨迹建议每条单独校准
- 如果两步路完全忽略 GPX 中的 `<ele>` 标签（用自己 DEM 计算海拔），此方法无效
- 校准点越多预测越准，建议 ≥ 2 个点；3-4 个点可达 ±5m 精度

## 示例输出

完整的路书示例见 `examples/route_report_example.md`（武汉-长沙 315.8km 骑行，6 段分段，2 天行程规划）。
