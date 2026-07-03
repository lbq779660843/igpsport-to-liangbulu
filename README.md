# igpsport-to-liangbulu

> 将 iGPSPORT/Garmin 等设备导出的 GPX 轨迹修正为在两步路等运动 APP 中显示正确的运动时长、距离和累计爬升。同时提供骑行路线分析工具，自动生成路书。

## 解决什么问题

从 iGPSPORT/Garmin 等设备导出 GPX 后导入两步路 APP，常出现以下问题：

| 问题 | 原因 |
|------|------|
| 运动时长偏长 | 两步路用总经过时间当运动耗时（不剔除停止段）|
| 累计爬升缩水（如 752m → 125m）| 两步路对海拔做内部滤波（滑动平均+阈值过滤）|
| 距离偶尔偏差 | 海拔平滑后路径投影变化 |

本工具用**反推校准**解决：用已知校准点（不同 scale 下 APP 显示的爬升值）反推 APP 的滤波参数，然后二分搜索精确的 scale 值，使 APP 输出 = 目标爬升。

## 包含工具

### 1. gpx_calibrate.py — 海拔反推校准

- **时间压缩**：剔除停止段（速度 < 2km/h），缩放运动段使总时长 = 目标运动时长
- **海拔反推校准**：网格搜索 APP 滤波参数（threshold + smooth_window），二分搜索最优 scale
- **差分增益**：仅放大有效海拔变化（> 0.15m），防止噪声爆炸

### 2. gpx_route_analyze.py — 骑行路线分析

- 自动按距离分段（默认 60km/段）
- 计算每段海拔剖面、累计爬升、坡度分布
- 识别道路类型（国道/省道/县道/城市道路）
- 生成骑行路书：路况评估、补给建议、风险提示、多日行程规划

## 安装

### 作为 Claude Code Skill 使用（推荐）

将本仓库克隆到 Claude Code 的 skills 目录：

```bash
# Windows
git clone https://github.com/lbq779660843/igpsport-to-liangbulu.git \
  "$env:USERPROFILE\.claude\skills\igpsport-to-liangbulu"

# macOS / Linux
git clone https://github.com/lbq779660843/igpsport-to-liangbulu.git \
  ~/.claude/skills/igpsport-to-liangbulu
```

安装后，在 Claude Code 中描述需求（如"把这个 GPX 修正后导入两步路"），Claude 会自动调用本 skill。

### 独立使用

```bash
git clone https://github.com/lbq779660843/igpsport-to-liangbulu.git
cd igpsport-to-liangbulu
```

依赖：Python 3.8+，仅标准库（无需 pip install）。

## 使用方法

### 海拔校准

```bash
python -u scripts/gpx_calibrate.py \
  -i 1.gpx -o wh_cs_calibrated.gpx \
  --moving-time 12:18:21 --distance 310.77 --ascent 752 \
  --calib 1.0,125 --calib 2.5,860 \
  --name "武汉-长沙骑行"
```

**参数说明：**

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

### 路线分析

```bash
python -u scripts/gpx_route_analyze.py \
  -i 1.gpx -o route_report.md \
  --name "武汉-长沙骑行" --day-km 220
```

**参数说明：**

| 参数 | 必填 | 说明 |
|------|------|------|
| `-i / --input` | 是 | 输入 GPX 文件 |
| `-o / --output` | 否 | 输出路书文件（默认 route_report.md）|
| `--name` | 否 | 路线名称（默认 骑行路线）|
| `--segment-km` | 否 | 分段距离 km（默认 60）|
| `--day-km` | 否 | 每日骑行距离 km（默认 180）|

## 校准流程（重要）

海拔校准需要**至少 2 个校准点**才能反推 APP 滤波参数。

### 第一次使用（无校准数据）

1. 用 `--calib 1.0,0` 生成 scale=1.0 的文件，导入两步路看显示的爬升值 X1
2. 用 `--calib 2.0,0` 生成 scale=2.0 的文件，导入两步路看显示的爬升值 X2
3. 用真实校准点重跑：`--calib 1.0,X1 --calib 2.0,X2 --ascent <目标爬升>`

### 迭代提高精度

如果首次校准后 APP 显示仍有偏差：

```bash
# 添加第 3 个校准点（实际显示的爬升值）
python -u scripts/gpx_calibrate.py \
  -i 1.gpx -o wh_cs_calibrated.gpx \
  --moving-time 12:18:21 --distance 310.77 --ascent 752 \
  --calib 1.0,125 --calib 2.5,860 --calib 2.43,764
```

校准点越多预测越准，建议 3-4 个点可达 ±5m 精度。

## 输出示例

完整的路书示例见 [`examples/route_report_example.md`](examples/route_report_example.md)（武汉-长沙 315.8km 骑行，6 段分段，2 天行程规划）。

路书包含：
- 全程概览（总距离、总爬升、海拔范围）
- 分段路况总表（道路类型、爬升、评分）
- 分段详情（坡度分布、起终点坐标）
- 行程规划（按日骑行安排、难度评估）
- 安全与装备建议

## 核心算法

### APP 海拔滤波模型

大多数运动 APP 对 GPX 海拔做两步处理：

1. **滑动平均平滑**（消除 GPS 海拔噪声）
2. **阈值过滤计算爬升**（忽略小于 threshold 的海拔变化）

本工具用校准数据反推这两个参数，建立 APP 的内部模型。

### 差分增益

```
if abs(delta_elevation) > 0.15:
    new_elev += delta * scale   # 放大有效变化
else:
    new_elev += delta           # 保留微小变化
```

避免直接乘法导致噪声被等比放大。

### 二分搜索

在 APP 滤波模型已知后，对 scale 二分搜索（60 次迭代，精度 1m），找到使 APP 输出 = 目标爬升的精确 scale 值。

## 注意事项

- 校准数据针对**特定 APP**，换 APP 需重新校准
- 不同轨迹的校准参数可能不同（地形不同 APP 平滑效果不同），长距离轨迹建议每条单独校准
- 如果两步路完全忽略 GPX 中的 `<ele>` 标签（用自己 DEM 计算海拔），此方法无效
- 每次运行会生成 `_params.json` 参数文件，记录校准结果供后续迭代使用

## 项目结构

```
igpsport-to-liangbulu/
├── SKILL.md                          # Claude Code skill 描述文件
├── README.md                         # 本文件
├── scripts/
│   ├── gpx_calibrate.py              # 海拔反推校准
│   └── gpx_route_analyze.py          # 骑行路线分析
└── examples/
    └── route_report_example.md       # 路书输出示例
```

## License

MIT
