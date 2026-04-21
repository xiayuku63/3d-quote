import sys
import os
import sqlite3
import base64
import tempfile
import trimesh
from main import run_prusaslicer_slice, prusaslicer_support_diff_stats, parse_prusaslicer_gcode_stats

def main():
    print("=== 开始端到端切片和 G-code 读取测试 ===")
    
    # 1. 生成测试模型
    stl_path = "test_model_integration.stl"
    print(f"\n1. 生成测试模型 {stl_path}...")
    mesh = trimesh.creation.box(extents=[30, 30, 30])
    mesh.export(stl_path)
    print("   模型生成成功。")
    
    # 2. 从数据库获取预设
    print("\n2. 获取切片预设...")
    preset_path = None
    try:
        conn = sqlite3.connect('app.db')
        row = conn.execute('SELECT content_b64 FROM slicer_presets ORDER BY id DESC LIMIT 1').fetchone()
        if row:
            content = base64.b64decode(row[0])
            tf = tempfile.NamedTemporaryFile(delete=False, suffix='.ini')
            tf.write(content)
            tf.close()
            preset_path = tf.name
            print(f"   成功获取预设并保存至: {preset_path}")
        else:
            print("   未找到预设，将不使用预设文件进行测试。")
    except Exception as e:
        print(f"   获取预设出错: {e}")
        
    # 3. 设置切片参数
    print("\n3. 准备切片参数...")
    extra_loads = [preset_path] if preset_path else []
    extra_sets = {
        '--layer-height': '0.15',
        '--fill-density': '15%',
        '--perimeters': '2'
    }
    print(f"   加载文件: {extra_loads}")
    print(f"   额外参数: {extra_sets}")
    
    # 4. 执行普通切片测试 (不带支撑)
    print("\n4. 执行普通切片测试 (不带支撑)...")
    gcode_path = "test_integration_output.gcode"
    extra_sets_no_support = {**extra_sets, '--support-material': '0', '--support-material-auto': '0'}
    try:
        st = run_prusaslicer_slice(stl_path, gcode_path, extra_loads=extra_loads, extra_sets=extra_sets_no_support)
        print(f"   切片成功！返回统计数据: {st}")
        
        # 验证 G-code 是否存在
        if os.path.exists(gcode_path):
            print(f"   G-code 文件 {gcode_path} 生成成功。文件大小: {os.path.getsize(gcode_path)} bytes")
            
            # 手动调用 parse_prusaslicer_gcode_stats 验证解析
            parsed_st = parse_prusaslicer_gcode_stats(gcode_path)
            print(f"   再次从文件解析的统计数据: {parsed_st}")
            
            if parsed_st.get("estimated_time_s"):
                print(f"   [验证通过] 成功解析到打印时间: {parsed_st['estimated_time_s']} 秒")
            else:
                print("   [验证失败] 无法从 G-code 中解析到打印时间！")
        else:
            print("   [验证失败] 切片命令成功返回但 G-code 文件未生成！")
    except Exception as e:
        print(f"   切片失败: {e}")
        
    # 5. 清理临时文件
    print("\n5. 清理临时文件...")
    try:
        if os.path.exists(stl_path): os.remove(stl_path)
        if os.path.exists(gcode_path): os.remove(gcode_path)
        if preset_path and os.path.exists(preset_path): os.remove(preset_path)
        print("   清理完成。")
    except Exception as e:
        print(f"   清理出错: {e}")

if __name__ == "__main__":
    main()
