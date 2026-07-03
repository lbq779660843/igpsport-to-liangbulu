#!/usr/bin/env python3
"""
GPX骑行路线分析 - 路况评估与行程规划

功能:
  1. 解析GPX轨迹, 自动分段(按距离/行政区划)
  2. 计算每段的海拔剖面、累计爬升、坡度分布
  3. 识别道路类型(国道/省道/县道/城市道路)
  4. 生成骑行路书: 路况评估、补给建议、风险提示、行程规划

用法:
  python gpx_route_analyze.py -i 1.gpx -o route_report.md [--day-km 180]
"""

import xml.etree.ElementTree as ET
from datetime import datetime
import math
import argparse
import os
import json


def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def parse_gpx(input_file):
    tree = ET.parse(input_file)
    root = tree.getroot()
    ns = {'ns': 'http://www.topografix.com/GPX/1/1'}
    trkpts = root.findall('.//ns:trkpt', ns)
    if not trkpts:
        raise ValueError("未找到轨迹点")

    points = []
    for pt in trkpts:
        lat = float(pt.get('lat'))
        lon = float(pt.get('lon'))
        ele_node = pt.find('ns:ele', ns)
        ele = float(ele_node.text) if ele_node is not None else None
        time_node = pt.find('ns:time', ns)
        t = datetime.fromisoformat(time_node.text.replace('Z', '+00:00')) if time_node is not None else None
        points.append({'lat': lat, 'lon': lon, 'ele': ele, 'time': t})
    return points


def calc_elevation_stats(elevations):
    """计算海拔相关统计"""
    valid = [e for e in elevations if e is not None]
    if len(valid) < 2:
        return {'min': 0, 'max': 0, 'ascent': 0, 'descent': 0, 'avg': 0}

    ascent = sum(max(0, valid[i] - valid[i - 1]) for i in range(1, len(valid)))
    descent = sum(max(0, valid[i - 1] - valid[i]) for i in range(1, len(valid)))
    return {
        'min': min(valid),
        'max': max(valid),
        'ascent': ascent,
        'descent': descent,
        'avg': sum(valid) / len(valid),
    }


def calc_grade_distribution(points, bin_width_m=100):
    """计算坡度分布"""
    grades = []
    for i in range(1, len(points)):
        if points[i]['ele'] is None or points[i - 1]['ele'] is None:
            continue
        dist = haversine(points[i - 1]['lat'], points[i - 1]['lon'],
                         points[i]['lat'], points[i]['lon'])
        if dist < 5:  # 忽略极短距离
            continue
        de = points[i]['ele'] - points[i - 1]['ele']
        grade = (de / dist) * 100
        grades.append(grade)

    if not grades:
        return {'flat_pct': 100, 'gentle_up_pct': 0, 'steep_up_pct': 0,
                'gentle_down_pct': 0, 'steep_down_pct': 0, 'max_up': 0, 'max_down': 0,
                'avg_up': 0, 'avg_down': 0}

    total = len(grades)
    flat = sum(1 for g in grades if abs(g) < 3)
    gentle_up = sum(1 for g in grades if 3 <= g < 6)
    steep_up = sum(1 for g in grades if g >= 6)
    gentle_down = sum(1 for g in grades if -6 < g <= -3)
    steep_down = sum(1 for g in grades if g <= -6)

    up_grades = [g for g in grades if g >= 3]
    down_grades = [g for g in grades if g <= -3]

    return {
        'flat_pct': flat / total * 100,
        'gentle_up_pct': gentle_up / total * 100,
        'steep_up_pct': steep_up / total * 100,
        'gentle_down_pct': gentle_down / total * 100,
        'steep_down_pct': steep_down / total * 100,
        'max_up': max(grades) if grades else 0,
        'max_down': min(grades) if grades else 0,
        'avg_up': sum(up_grades) / len(up_grades) if up_grades else 0,
        'avg_down': sum(down_grades) / len(down_grades) if down_grades else 0,
    }


def auto_segment(points, segment_km=60):
    """按距离自动分段"""
    segments = []
    current = [points[0]]
    cum_dist = 0.0

    for i in range(1, len(points)):
        d = haversine(points[i - 1]['lat'], points[i - 1]['lon'],
                      points[i]['lat'], points[i]['lon'])
        cum_dist += d
        current.append(points[i])

        if cum_dist / 1000 >= segment_km and len(current) > 10:
            segments.append(current)
            current = [points[i]]
            cum_dist = 0.0

    if len(current) > 1:
        segments.append(current)

    return segments


def infer_road_type(points_segment):
    """根据轨迹特征推断道路类型"""
    n = len(points_segment)
    if n < 2:
        return '未知'

    # 计算平均点间距
    dists = []
    for i in range(1, n):
        d = haversine(points_segment[i - 1]['lat'], points_segment[i - 1]['lon'],
                      points_segment[i]['lat'], points_segment[i]['lon'])
        dists.append(d)

    avg_dist = sum(dists) / len(dists) if dists else 0
    total_dist = sum(dists)

    # 计算方向变化频率(弯道密度)
    direction_changes = 0
    for i in range(2, n):
        d1_lat = points_segment[i - 1]['lat'] - points_segment[i - 2]['lat']
        d1_lon = points_segment[i - 1]['lon'] - points_segment[i - 2]['lon']
        d2_lat = points_segment[i]['lat'] - points_segment[i - 1]['lat']
        d2_lon = points_segment[i]['lon'] - points_segment[i - 1]['lon']
        cross = d1_lat * d2_lon - d1_lon * d2_lat
        if abs(cross) > 1e-8:
            direction_changes += 1

    bend_density = direction_changes / n if n > 0 else 0

    # 计算海拔变化
    eles = [p['ele'] for p in points_segment if p['ele'] is not None]
    ele_range = max(eles) - min(eles) if len(eles) >= 2 else 0

    # 推断逻辑
    if total_dist < 3000 and avg_dist < 8:
        return '城市道路'
    elif bend_density > 0.7 and ele_range > 100:
        return '县道/乡道(山路)'
    elif bend_density > 0.5 and ele_range > 50:
        return '省道(丘陵)'
    elif bend_density > 0.5:
        return '省道(平原)'
    elif ele_range > 80:
        return '国道(丘陵)'
    elif avg_dist > 15:
        return '国道(平原)/快速路'
    else:
        return '省道/国道'


def grade_road_condition(points_segment, grade_dist):
    """评估路况等级(1-5星)"""
    score = 3.0  # 基础分

    # 坡度越平越好骑
    flat_pct = grade_dist['flat_pct']
    steep_pct = grade_dist['steep_up_pct'] + grade_dist['steep_down_pct']
    if flat_pct > 70:
        score += 0.5
    elif flat_pct > 50:
        score += 0.2
    if steep_pct > 20:
        score -= 0.5
    elif steep_pct > 10:
        score -= 0.2

    # 海拔变化越大越难
    eles = [p['ele'] for p in points_segment if p['ele'] is not None]
    if len(eles) >= 2:
        ele_range = max(eles) - min(eles)
        if ele_range > 200:
            score -= 0.3
        elif ele_range > 100:
            score -= 0.1
        elif ele_range < 30:
            score += 0.2

    # 道路类型
    road_type = infer_road_type(points_segment)
    if '快速路' in road_type or '省道(平原)' in road_type:
        score += 0.3
    elif '山路' in road_type:
        score -= 0.3
    elif '城市' in road_type:
        score -= 0.1

    return max(1.0, min(5.0, score))


def generate_route_report(segments, output_file, track_name="骑行路线",
                          day_km=180):
    """生成骑行路书"""
    lines = []
    lines.append(f"# {track_name} - 骑行路书\n")

    # ---- 全程概览 ----
    total_dist = 0
    total_ascent = 0
    all_eles = []

    for seg in segments:
        for i in range(1, len(seg)):
            total_dist += haversine(seg[i - 1]['lat'], seg[i - 1]['lon'],
                                    seg[i]['lat'], seg[i]['lon'])
        eles = [p['ele'] for p in seg if p['ele'] is not None]
        all_eles.extend(eles)
        stats = calc_elevation_stats(eles)
        total_ascent += stats['ascent']

    elapsed = None
    if segments[0][0]['time'] and segments[-1][-1]['time']:
        elapsed = (segments[-1][-1]['time'] - segments[0][0]['time']).total_seconds()

    lines.append("## 全程概览\n")
    lines.append(f"| 项目 | 数值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 总距离 | **{total_dist / 1000:.1f}km** |")
    lines.append(f"| 总爬升 | **{total_ascent:.0f}m** |")
    if all_eles:
        lines.append(f"| 海拔范围 | {min(all_eles):.0f}m ~ {max(all_eles):.0f}m |")
    if elapsed:
        h = int(elapsed // 3600)
        m = int((elapsed % 3600) // 60)
        lines.append(f"| 记录时长 | {h}h{m}m |")
    lines.append(f"| 分段数 | {len(segments)}段 |")
    lines.append("")

    # ---- 分段详情 ----
    cum_km = 0.0
    seg_reports = []

    for idx, seg in enumerate(segments):
        seg_dist = sum(haversine(seg[i - 1]['lat'], seg[i - 1]['lon'],
                                  seg[i]['lat'], seg[i]['lon'])
                       for i in range(1, len(seg)))
        seg_km = seg_dist / 1000
        cum_km_end = cum_km + seg_km

        eles = [p['ele'] for p in seg if p['ele'] is not None]
        stats = calc_elevation_stats(eles)
        grade_dist = calc_grade_distribution(seg)
        road_type = infer_road_type(seg)
        rating = grade_road_condition(seg, grade_dist)

        # 起终点坐标
        start_pt = seg[0]
        end_pt = seg[-1]

        report = {
            'idx': idx + 1,
            'start_km': cum_km,
            'end_km': cum_km_end,
            'distance': seg_km,
            'road_type': road_type,
            'rating': rating,
            'stats': stats,
            'grade_dist': grade_dist,
            'start_coord': f"({start_pt['lat']:.4f}, {start_pt['lon']:.4f})",
            'end_coord': f"({end_pt['lat']:.4f}, {end_pt['lon']:.4f})",
        }
        seg_reports.append(report)
        cum_km = cum_km_end

    # 输出分段表
    lines.append("## 分段路况总表\n")
    lines.append("| 段 | 里程 | 累计 | 道路类型 | 爬升 | 评分 |")
    lines.append("|----|------|------|----------|------|------|")
    for r in seg_reports:
        stars = "★" * int(r['rating']) + "☆" * (5 - int(r['rating']))
        lines.append(
            f"| {r['idx']} | {r['distance']:.1f}km | "
            f"{r['end_km']:.0f}km | {r['road_type']} | "
            f"{r['stats']['ascent']:.0f}m↑ {r['stats']['descent']:.0f}m↓ | "
            f"{stars} |"
        )
    lines.append("")

    # 详细分段
    lines.append("## 分段详情\n")
    for r in seg_reports:
        gd = r['grade_dist']
        lines.append(f"### 第{r['idx']}段 ({r['start_km']:.0f}~{r['end_km']:.0f}km)")
        lines.append(f"")
        lines.append(f"- **距离**: {r['distance']:.1f}km")
        lines.append(f"- **道路类型**: {r['road_type']}")
        lines.append(f"- **海拔**: {r['stats']['min']:.0f}m ~ {r['stats']['max']:.0f}m (均值{r['stats']['avg']:.0f}m)")
        lines.append(f"- **爬升/下降**: {r['stats']['ascent']:.0f}m↑ / {r['stats']['descent']:.0f}m↓")
        lines.append(f"- **坡度分布**: 平路{gd['flat_pct']:.0f}% / 缓上{gd['gentle_up_pct']:.0f}% / 陡上{gd['steep_up_pct']:.0f}% / 缓下{gd['gentle_down_pct']:.0f}% / 陡下{gd['steep_down_pct']:.0f}%")
        if gd['avg_up'] > 0:
            lines.append(f"- **平均上坡坡度**: {gd['avg_up']:.1f}% (最大{gd['max_up']:.1f}%)")
        if gd['avg_down'] < 0:
            lines.append(f"- **平均下坡坡度**: {gd['avg_down']:.1f}% (最大{gd['max_down']:.1f}%)")
        lines.append(f"- **起终点**: {r['start_coord']} → {r['end_coord']}")
        lines.append("")

    # ---- 行程规划 ----
    lines.append("## 行程规划\n")

    day = 1
    day_dist = 0
    day_ascent = 0
    day_segs = []

    for r in seg_reports:
        day_dist += r['distance']
        day_ascent += r['stats']['ascent']
        day_segs.append(r)

        if day_dist >= day_km or r == seg_reports[-1]:
            lines.append(f"### Day {day}: 第{day_segs[0]['idx']}~{day_segs[-1]['idx']}段")
            lines.append(f"")
            lines.append(f"- **距离**: {day_dist:.0f}km")
            lines.append(f"- **爬升**: {day_ascent:.0f}m")
            seg_names = [f"第{r2['idx']}段" for r2 in day_segs]
            lines.append(f"- **路段**: {', '.join(seg_names)}")

            # 路线描述
            route_desc_parts = []
            for r2 in day_segs:
                rt = r2['road_type']
                asc = r2['stats']['ascent']
                desc_str = f"{r2['distance']:.0f}km"
                if asc > 100:
                    desc_str += f" ({asc:.0f}m↑)"
                route_desc_parts.append(f"{rt} {desc_str}")
                seg_names.append(f"第{r2['idx']}段")
            lines.append(f"- **路段**: {', '.join(seg_names)}")
            lines.append(f"- **路线**: {' -> '.join(route_desc_parts)}")

            # 难度评估
            hardest = max(day_segs, key=lambda x: x['stats']['ascent'] / max(x['distance'], 1))
            easiest = min(day_segs, key=lambda x: x['stats']['ascent'] / max(x['distance'], 1))
            lines.append(f"- **最难点**: 第{hardest['idx']}段 ({hardest['road_type']}, {hardest['stats']['ascent']:.0f}m↑)")
            lines.append(f"- **最易点**: 第{easiest['idx']}段 ({easiest['road_type']})")

            # 骑行建议
            if day_ascent > 800:
                lines.append(f"- **建议**: 爬升大, 早出发, 保留体力应对起伏; 建议前34T小盘+后34T大飞")
            elif day_ascent > 400:
                lines.append(f"- **建议**: 中等难度, 有起伏但可控; 合理分配体力")
            else:
                lines.append(f"- **建议**: 以平路为主, 可适当加速巡航")

            lines.append("")
            day += 1
            day_dist = 0
            day_ascent = 0
            day_segs = []

    # ---- 海拔剖面数据(供绘图) ----
    lines.append("## 海拔剖面数据\n")
    lines.append("```")
    lines.append("累计km | 海拔m")
    cum = 0
    sample_interval = max(1, len(segments[0]) // 50)
    all_points = [p for seg in segments for p in seg]
    for i, p in enumerate(all_points):
        if i > 0:
            cum += haversine(all_points[i - 1]['lat'], all_points[i - 1]['lon'],
                             p['lat'], p['lon'])
        if i % sample_interval == 0 and p['ele'] is not None:
            lines.append(f"{cum / 1000:.1f} | {p['ele']:.0f}")
    lines.append("```\n")

    # ---- 安全与装备 ----
    lines.append("## 安全与装备建议\n")

    max_steep = max(r['grade_dist']['max_up'] for r in seg_reports)
    total_asc = sum(r['stats']['ascent'] for r in seg_reports)
    has_urban = any('城市' in r['road_type'] for r in seg_reports)
    has_mountain = any('山路' in r['road_type'] for r in seg_reports)

    lines.append("### 必备装备")
    lines.append("- 头盔、尾灯、反光背心")
    if total_asc > 500:
        lines.append("- 建议齿比: **前34T小盘 + 后34T大飞** (应对连续起伏)")
    lines.append("- 备胎、撬胎棒、迷你打气筒")

    lines.append("\n### 安全注意")
    for r in seg_reports:
        if r['grade_dist']['steep_up_pct'] > 10:
            lines.append(f"- **第{r['idx']}段**: 陡坡较多(最大{r['grade_dist']['max_up']:.1f}%), 下坡控速")
        if '国道' in r['road_type']:
            lines.append(f"- **第{r['idx']}段**: 国道货车多, 靠右骑行, 注意会车")
        if '城市' in r['road_type']:
            lines.append(f"- **第{r['idx']}段**: 城区人多车杂, 注意行人和电动车")

    lines.append("\n### 补给建议")
    lines.append(f"- 携带2瓶水 + 能量胶/能量棒")
    lines.append(f"- 沿路集镇补给点(省道/国道段通常5~15km一个)")
    if has_mountain:
        lines.append("- 山路段补给少, 进入前补满水")

    lines.append("\n### 避峰建议")
    lines.append("- 建议**早6点前出发**, 避开正午暴晒+货车高峰")
    lines.append("- 17点前到达目的地, 避免夜骑")

    # 写文件
    report_text = '\n'.join(lines)
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(report_text)

    return report_text, seg_reports


def main():
    parser = argparse.ArgumentParser(
        description='GPX骑行路线分析 - 路况评估与行程规划',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 基础分析
  python gpx_route_analyze.py -i 1.gpx -o route_report.md

  # 指定每天骑行距离(用于行程规划)
  python gpx_route_analyze.py -i 1.gpx -o route_report.md --day-km 150

  # 指定分段距离(默认60km一段)
  python gpx_route_analyze.py -i 1.gpx -o route_report.md --segment-km 80
        """)
    parser.add_argument('-i', '--input', required=True, help='输入GPX文件')
    parser.add_argument('-o', '--output', default='route_report.md',
                        help='输出路书文件 (默认route_report.md)')
    parser.add_argument('--name', default='骑行路线', help='路线名称')
    parser.add_argument('--segment-km', type=float, default=60,
                        help='分段距离(km, 默认60)')
    parser.add_argument('--day-km', type=float, default=180,
                        help='每日骑行距离(km, 默认180, 用于行程规划)')

    args = parser.parse_args()

    print("=" * 60)
    print("GPX骑行路线分析")
    print("=" * 60)
    print(f"  输入: {args.input}")
    print(f"  分段距离: {args.segment_km}km")
    print(f"  每日骑行: {args.day_km}km")

    # 1. 解析
    print(f"\n[1/3] 解析GPX...")
    points = parse_gpx(args.input)
    print(f"  轨迹点数: {len(points)}")

    # 2. 分段
    print(f"\n[2/3] 自动分段(每{args.segment_km}km)...")
    segments = auto_segment(points, args.segment_km)
    total = sum(
        haversine(seg[i - 1]['lat'], seg[i - 1]['lon'], seg[i]['lat'], seg[i]['lon'])
        for seg in segments for i in range(1, len(seg))
    )
    print(f"  分为 {len(segments)} 段, 总长 {total / 1000:.1f}km")

    for idx, seg in enumerate(segments):
        d = sum(haversine(seg[i - 1]['lat'], seg[i - 1]['lon'],
                          seg[i]['lat'], seg[i]['lon'])
                for i in range(1, len(seg)))
        eles = [p['ele'] for p in seg if p['ele'] is not None]
        asc = sum(max(0, eles[i] - eles[i - 1]) for i in range(1, len(eles))) if len(eles) > 1 else 0
        road = infer_road_type(seg)
        print(f"  段{idx + 1}: {d / 1000:.1f}km, 爬升{asc:.0f}m, {road}")

    # 3. 生成报告
    print(f"\n[3/3] 生成路书...")
    report, seg_reports = generate_route_report(
        segments, args.output, args.name, args.day_km
    )
    print(f"  路书已写出: {args.output} ({os.path.getsize(args.output) / 1024:.0f}KB)")

    # 保存分段数据(JSON)
    json_file = args.output.replace('.md', '_segments.json')
    json_data = []
    cum_km = 0
    for seg, r in zip(segments, seg_reports):
        json_data.append({
            'segment': r['idx'],
            'start_km': round(r['start_km'], 1),
            'end_km': round(r['end_km'], 1),
            'distance_km': round(r['distance'], 1),
            'road_type': r['road_type'],
            'ascent_m': round(r['stats']['ascent'], 0),
            'descent_m': round(r['stats']['descent'], 0),
            'ele_min': round(r['stats']['min'], 0) if r['stats']['min'] else None,
            'ele_max': round(r['stats']['max'], 0) if r['stats']['max'] else None,
            'grade_max_up': round(r['grade_dist']['max_up'], 1),
            'grade_max_down': round(r['grade_dist']['max_down'], 1),
            'rating': round(r['rating'], 1),
        })
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)
    print(f"  分段数据: {json_file}")


if __name__ == '__main__':
    main()
