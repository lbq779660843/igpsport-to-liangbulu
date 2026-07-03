#!/usr/bin/env python3
"""
GPX轨迹修正 - 两步路海拔反推校准版

解决问题:
  两步路等运动APP对GPX海拔做内部滤波(滑动平均+阈值),
  导致文件中752m爬升 → APP显示125m。

算法:
  用已知校准点(不同scale下APP显示的爬升值)反推APP的滤波参数,
  然后二分搜索精确的scale值, 使APP输出 = 目标爬升。

用法:
  python gpx_calibrate.py --input 1.gpx --output result.gpx \
      --moving-time 12:18:21 --distance 310.77 --ascent 752 \
      --calib 1.0,125 --calib 2.5,860
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import argparse
import math
import os
import json


# ============================================================
# 基础工具
# ============================================================

def haversine(lon1, lat1, lon2, lat2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def calc_cumulative_ascent(elevations):
    return sum(max(0, elevations[i] - elevations[i - 1]) for i in range(1, len(elevations)))


# ============================================================
# APP海拔滤波器模拟与校准
# ============================================================

class AppFilterSimulator:
    """
    运动APP海拔处理模拟器

    大多数运动APP(GPX导入后)对海拔做两步处理:
    1. 滑动平均平滑 (消除GPS海拔噪声)
    2. 阈值过滤计算爬升 (忽略小于threshold的海拔变化)

    本模拟器用校准数据反推这两个参数, 然后预测任意scale下的APP输出。
    """

    def __init__(self, threshold=2.0, smooth_window=11, pre_smooth_window=5):
        self.threshold = threshold
        self.smooth_window = smooth_window
        self.pre_smooth_window = pre_smooth_window

    def simulate(self, elevations):
        """模拟APP的处理: 滑动平均 + 阈值过滤"""
        n = len(elevations)

        # 1. 滑动平均
        w = self.smooth_window
        smoothed = []
        for i in range(n):
            start = max(0, i - w // 2)
            end = min(n, i + w // 2 + 1)
            smoothed.append(sum(elevations[start:end]) / (end - start))

        # 2. 阈值过滤计算爬升
        cum_asc = 0
        last_valid = smoothed[0]
        for i in range(1, n):
            diff = smoothed[i] - last_valid
            if diff > self.threshold:
                cum_asc += diff
                last_valid = smoothed[i]
            elif diff < -self.threshold:
                last_valid = smoothed[i]

        return cum_asc

    def apply_scale(self, elevations, scale):
        """应用差分增益: 放大有效海拔变化, 保持微小变化不变"""
        new_elev = [elevations[0]]
        for i in range(1, len(elevations)):
            delta = elevations[i] - elevations[i - 1]
            if abs(delta) > 0.15:
                new_elev.append(new_elev[-1] + delta * scale)
            else:
                new_elev.append(new_elev[-1] + delta)

        # 预平滑(5点滑窗)
        w = self.pre_smooth_window
        smoothed = []
        for i in range(len(new_elev)):
            s = max(0, i - w // 2)
            e = min(len(new_elev), i + w // 2 + 1)
            smoothed.append(sum(new_elev[s:e]) / (e - s))
        return smoothed

    def calibrate(self, raw_elevations, calibrations):
        """
        用已知校准点反推APP滤波参数

        calibrations: [(manual_scale, APP显示爬升), ...]
        网格搜索找最佳 threshold + smooth_window + pre_smooth_window
        """
        print("  校准APP滤波模型...")

        best_params = None
        best_error = float('inf')

        thresholds = [x * 0.2 for x in range(1, 151)]  # 0.2 ~ 30.0
        windows = [3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 25, 31, 41, 51]
        pre_windows = [3, 5, 7, 9, 11]

        total_combos = len(thresholds) * len(windows) * len(pre_windows)
        print(f"  搜索空间: {total_combos} 种参数组合...")

        for threshold in thresholds:
            for smooth_window in windows:
                for pre_smooth in pre_windows:
                    self.threshold = threshold
                    self.smooth_window = smooth_window
                    self.pre_smooth_window = pre_smooth

                    total_error = 0
                    for scale, expected_asc in calibrations:
                        scaled = self.apply_scale(raw_elevations, scale)
                        predicted = self.simulate(scaled)
                        total_error += (predicted - expected_asc) ** 2

                    if total_error < best_error:
                        best_error = total_error
                        best_params = (threshold, smooth_window, pre_smooth)

        self.threshold, self.smooth_window, self.pre_smooth_window = best_params
        rmse = math.sqrt(best_error / len(calibrations))
        print(f"  校准结果: threshold={self.threshold:.1f}m, "
              f"smooth_window={self.smooth_window}, pre_smooth={self.pre_smooth_window}")
        print(f"  拟合误差(RMSE): {rmse:.1f}m")

        for scale, expected in calibrations:
            scaled = self.apply_scale(raw_elevations, scale)
            predicted = self.simulate(scaled)
            print(f"    scale={scale:.2f} -> 预测{predicted:.0f}m vs 实际{expected}m "
                  f"(偏差{predicted - expected:+.0f}m)")

        return best_params

    def find_optimal_scale(self, raw_elevations, target_ascent,
                           lo=1.0, hi=5.0, tolerance=1.0):
        """二分搜索: 找到使APP输出约等于target_ascent的scale"""
        print(f"\n  二分搜索最优scale (目标: APP显示{target_ascent}m)...")

        scaled_lo = self.apply_scale(raw_elevations, lo)
        pred_lo = self.simulate(scaled_lo)
        scaled_hi = self.apply_scale(raw_elevations, hi)
        pred_hi = self.simulate(scaled_hi)

        if pred_lo > target_ascent:
            print(f"  警告: scale={lo}已超过目标({pred_lo:.0f}m>{target_ascent}m)")
            return lo
        if pred_hi < target_ascent:
            print(f"  警告: scale={hi}仍未达目标({pred_hi:.0f}m<{target_ascent}m), 扩大搜索范围")
            return self.find_optimal_scale(raw_elevations, target_ascent, lo, hi * 2, tolerance)

        mid = (lo + hi) / 2
        for _ in range(60):
            mid = (lo + hi) / 2
            scaled = self.apply_scale(raw_elevations, mid)
            predicted = self.simulate(scaled)

            if abs(predicted - target_ascent) < tolerance:
                break
            if predicted < target_ascent:
                lo = mid
            else:
                hi = mid

        scaled = self.apply_scale(raw_elevations, mid)
        predicted = self.simulate(scaled)
        print(f"  最优scale={mid:.4f}, 预测APP爬升={predicted:.1f}m "
              f"(偏差{predicted - target_ascent:+.1f}m)")
        return mid


# ============================================================
# GPX解析与写入
# ============================================================

def parse_gpx(input_file):
    tree = ET.parse(input_file)
    root = tree.getroot()
    ns = {'ns': 'http://www.topografix.com/GPX/1/1'}
    trkpts = root.findall('.//ns:trkpt', ns)
    if not trkpts:
        raise ValueError("未找到轨迹点, 请检查GPX格式")

    points = []
    for pt in trkpts:
        lat = float(pt.get('lat'))
        lon = float(pt.get('lon'))
        ele_node = pt.find('ns:ele', ns)
        ele = float(ele_node.text) if ele_node is not None else 0.0
        time_node = pt.find('ns:time', ns)
        t = datetime.fromisoformat(time_node.text.replace('Z', '+00:00')) if time_node is not None else None
        points.append({'lat': lat, 'lon': lon, 'ele': ele, 'time': t})
    return points


def write_gpx(points, elevations, output_file, track_name="Track"):
    ET.register_namespace('', "http://www.topografix.com/GPX/1/1")
    gpx = ET.Element('gpx', version="1.1", creator="iGPSPORT",
                     xmlns="http://www.topografix.com/GPX/1/1")
    trk = ET.SubElement(gpx, 'trk')
    ET.SubElement(trk, 'name').text = track_name
    trkseg = ET.SubElement(trk, 'trkseg')

    for i, p in enumerate(points):
        trkpt = ET.SubElement(trkseg, 'trkpt',
                              lat=f"{p['lat']:.7f}", lon=f"{p['lon']:.7f}")
        ET.SubElement(trkpt, 'ele').text = f"{elevations[i]:.2f}"
        t = p.get('new_time') or p['time']
        if t:
            ET.SubElement(trkpt, 'time').text = t.strftime('%Y-%m-%dT%H:%M:%SZ')

    tree = ET.ElementTree(gpx)
    tree.write(output_file, encoding='utf-8', xml_declaration=True)


# ============================================================
# 时间压缩
# ============================================================

def compress_timeline(points, target_moving_seconds, speed_threshold_kmh=2.0):
    """智能时间压缩: 剔除停止段 + 运动段缩放"""
    if len(points) < 2 or points[0]['time'] is None:
        for p in points:
            p['new_time'] = p['time']
        return points

    moving_deltas = []
    for i in range(1, len(points)):
        p1, p2 = points[i - 1], points[i]
        dt = (p2['time'] - p1['time']).total_seconds()
        dist = haversine(p1['lon'], p1['lat'], p2['lon'], p2['lat'])
        speed = (dist / dt * 3.6) if dt > 0 else 0
        moving_deltas.append({'dt': dt, 'is_moving': speed >= speed_threshold_kmh})

    total_moving = sum(d['dt'] for d in moving_deltas if d['is_moving'])
    total_stopped = sum(d['dt'] for d in moving_deltas if not d['is_moving'])
    time_scale = target_moving_seconds / total_moving if total_moving > 0 else 1.0

    print(f"  原始总时长: {(total_moving + total_stopped)/3600:.2f}h")
    print(f"  运动段: {total_moving/3600:.2f}h, 停止段: {total_stopped/3600:.2f}h")
    print(f"  时间缩放系数: {time_scale:.6f}")

    new_time = points[0]['time']
    for i in range(len(points)):
        points[i]['new_time'] = new_time
        if i < len(moving_deltas):
            if moving_deltas[i]['is_moving']:
                new_time += timedelta(seconds=moving_deltas[i]['dt'] * time_scale)

    return points


# ============================================================
# 主流水线
# ============================================================

def process_track(input_file, output_file,
                  target_moving_time_str, target_distance_km, target_ascent_m,
                  calibrations, track_name="Track",
                  speed_threshold=2.0):
    h, m, s = map(int, target_moving_time_str.split(':'))
    target_seconds = h * 3600 + m * 60 + s

    print("=" * 60)
    print("GPX轨迹修正 - APP海拔反推校准版")
    print("=" * 60)
    print(f"  输入文件: {input_file}")
    print(f"  输出文件: {output_file}")
    print(f"  目标运动时长: {target_moving_time_str} ({target_seconds}s)")
    print(f"  目标骑行距离: {target_distance_km}km")
    print(f"  目标累计爬升: {target_ascent_m}m")
    print(f"  校准数据: {calibrations}")

    # 1. 解析
    print(f"\n[1/4] 解析GPX...")
    points = parse_gpx(input_file)
    raw_elevations = [p['ele'] for p in points]
    raw_asc = calc_cumulative_ascent(raw_elevations)
    raw_dist = sum(haversine(points[i-1]['lon'], points[i-1]['lat'],
                             points[i]['lon'], points[i]['lat'])
                   for i in range(1, len(points)))
    raw_elapsed = (points[-1]['time'] - points[0]['time']).total_seconds()
    print(f"  轨迹点数: {len(points)}")
    print(f"  原始距离: {raw_dist/1000:.2f}km")
    print(f"  原始爬升: {raw_asc:.1f}m")
    print(f"  原始时长: {raw_elapsed/3600:.2f}h")

    # 2. 时间压缩
    print(f"\n[2/4] 时间压缩...")
    points = compress_timeline(points, target_seconds, speed_threshold)

    # 3. 海拔校准
    print(f"\n[3/4] 海拔校准...")
    sim = AppFilterSimulator()
    sim.calibrate(raw_elevations, calibrations)
    optimal_scale = sim.find_optimal_scale(raw_elevations, target_ascent_m)

    optimal_elevations = sim.apply_scale(raw_elevations, optimal_scale)
    file_asc = calc_cumulative_ascent(optimal_elevations)
    predicted_app = sim.simulate(optimal_elevations)

    print(f"\n  文件内爬升: {file_asc:.1f}m")
    print(f"  预测APP显示: {predicted_app:.1f}m")

    # 4. 写入
    print(f"\n[4/4] 写入GPX...")
    write_gpx(points, optimal_elevations, output_file, track_name)

    # 验证
    elapsed = (points[-1]['new_time'] - points[0]['new_time']).total_seconds()
    avg_spd = raw_dist / 1000 / (elapsed / 3600)
    eh = int(elapsed // 3600)
    em2 = int((elapsed % 3600) // 60)
    es2 = int(elapsed % 60)

    print(f"\n{'=' * 60}")
    print("输出验证:")
    print(f"{'=' * 60}")
    print(f"  文件: {output_file} ({os.path.getsize(output_file)/1024:.0f}KB)")
    print(f"  距离: {raw_dist/1000:.2f}km")
    print(f"  时长: {eh:02d}:{em2:02d}:{es2:02d}")
    print(f"  均速: {avg_spd:.2f}km/h")
    print(f"  文件内爬升: {file_asc:.1f}m")
    print(f"  预测APP爬升: {predicted_app:.1f}m (目标: {target_ascent_m}m)")
    print(f"  最优scale: {optimal_scale:.4f}")

    # 预测表
    print(f"\nscale预测表:")
    test_scales = [1.0, 1.5, 2.0]
    if optimal_scale not in test_scales:
        test_scales.append(optimal_scale)
    test_scales.extend([2.5, 3.0])
    for s in sorted(set(test_scales)):
        elev = sim.apply_scale(raw_elevations, s)
        pred = sim.simulate(elev)
        tag = " <-- 最优" if abs(s - optimal_scale) < 0.01 else ""
        print(f"  scale={s:.2f} -> APP显示{pred:.0f}m{tag}")

    # 校准提示
    print(f"\n{'=' * 60}")
    print("校准迭代:")
    print(f"{'=' * 60}")
    print(f"  导入APP后如果实际爬升偏差大, 添加校准点重跑:")
    print(f"  --calib {optimal_scale:.4f},实际显示的爬升值")
    print(f"  校准点越多预测越准, 建议>=2个点。")

    # 保存参数文件供下次校准
    params_file = output_file.replace('.gpx', '_params.json')
    params = {
        'input_file': input_file,
        'target_moving_time': target_moving_time_str,
        'target_distance_km': target_distance_km,
        'target_ascent_m': target_ascent_m,
        'calibrations': calibrations,
        'optimal_scale': optimal_scale,
        'predicted_app_ascent': predicted_app,
        'simulator_params': {
            'threshold': sim.threshold,
            'smooth_window': sim.smooth_window,
            'pre_smooth_window': sim.pre_smooth_window,
        }
    }
    with open(params_file, 'w', encoding='utf-8') as f:
        json.dump(params, f, indent=2, ensure_ascii=False)
    print(f"\n  参数已保存: {params_file}")

    return optimal_scale


# ============================================================
# 命令行入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description='GPX轨迹修正 - APP海拔反推校准版',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 基础用法: 2个校准点
  python gpx_calibrate.py -i 1.gpx -o result.gpx \\
      --moving-time 12:18:21 --distance 310.77 --ascent 752 \\
      --calib 1.0,125 --calib 2.5,860

  # 迭代校准: 加入第3个校准点提高精度
  python gpx_calibrate.py -i 1.gpx -o result.gpx \\
      --moving-time 12:18:21 --distance 310.77 --ascent 752 \\
      --calib 1.0,125 --calib 2.5,860 --calib 2.43,764
        """)
    parser.add_argument('-i', '--input', required=True, help='输入GPX文件')
    parser.add_argument('-o', '--output', default='calibrated.gpx', help='输出GPX文件')
    parser.add_argument('--moving-time', required=True,
                        help='目标运动时长 (HH:MM:SS)')
    parser.add_argument('--distance', type=float, required=True,
                        help='目标骑行距离 (km)')
    parser.add_argument('--ascent', type=float, required=True,
                        help='目标累计爬升 (m)')
    parser.add_argument('--calib', action='append', required=True,
                        help='校准点 scale,app显示爬升 (如: 1.0,125)')
    parser.add_argument('--name', default='Track', help='轨迹名称')
    parser.add_argument('--speed-threshold', type=float, default=2.0,
                        help='运动速度阈值 (km/h, 默认2.0)')

    args = parser.parse_args()

    calibrations = []
    for c in args.calib:
        parts = c.split(',')
        if len(parts) != 2:
            print(f"错误: 校准点格式应为 scale,ascent (如 1.0,125), 得到: {c}")
            return
        calibrations.append((float(parts[0]), float(parts[1])))

    process_track(
        input_file=args.input,
        output_file=args.output,
        target_moving_time_str=args.moving_time,
        target_distance_km=args.distance,
        target_ascent_m=args.ascent,
        calibrations=calibrations,
        track_name=args.name,
        speed_threshold=args.speed_threshold,
    )


if __name__ == '__main__':
    main()
