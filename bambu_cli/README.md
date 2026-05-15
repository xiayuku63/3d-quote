# bambu_cli — Pure CLI BambuStudio Slicer

剥离 wxWidgets GUI 依赖，只链 `libslic3r` 的纯命令行切片工具。

## 架构

```
BambuStudio 源码 (git clone)
├── src/libslic3r/     ← 核心库 (纯 C++, 零 wx 依赖)
├── src/slic3r/GUI/    ← wxWidgets GUI (本工具不使用)
├── bambu_cli/         ← 【本目录】纯 CLI 入口
│   ├── cli_main.cpp   # 主程序
│   ├── CMakeLists.txt # 构建配置
│   ├── Dockerfile     # Docker 构建
│   └── __init__.py    # Python wrapper (drop-in for pricer3d)
└── ...
```

## 工作路径

### Phase 1: 获取源码 (你来做，我网络受限)

```bash
# 从 GitHub 下载 BambuStudio 源码
git clone --depth 1 https://github.com/bambulab/BambuStudio.git bambu_studio_src
cd bambu_studio_src

# 把我写好的 CLI 代码放进去
cp -r /path/to/pricer3d/bambu_cli ./
```

> ⚠️ GitHub 在我的 WSL 环境中 DNS 被劫持 (198.18.0.x)，无法 clone。你需要在
> 能正常访问 GitHub 的机器上下载，或者通过镜像/代理获取。

### Phase 2: 构建验证 (已写好的 cmake 配置)

```bash
cd bambu_studio_src
mkdir -p build && cd build
cmake .. \
  -DCMAKE_BUILD_TYPE=Release \
  -DSLIC3R_BUILD_CLI=ON \
  -DSLIC3R_BUILD_GUI=OFF \
  -DSLIC3R_STATIC=OFF
cmake --build . --target bambu_cli -j$(nproc)
```

**首次编译耗时**: ~30-60min (libslic3r 依赖 Boost/Eigen/TBB/NLopt)

### Phase 3: Docker 集成

```bash
docker build -t bambu_cli -f bambu_cli/Dockerfile .
docker run --rm -v "$PWD/models:/work" -v "$PWD/profiles:/profiles" \
  bambu_cli \
  --printer /profiles/bambu/machine.json \
  --process /profiles/bambu/process.json \
  --filament /profiles/bambu/filament.json \
  --output /work/out.gcode \
  --stats \
  /work/model.stl
```

### Phase 4: 集成到 pricer3d

`bambu_cli/__init__.py` 提供与 `parser/prusa_slicer.py` 完全一致的接口。在 `calculator/cost.py` 中只需添加一个分支：

```python
# 在 calculate_cost() 中，PrusaSlicer 分支之前:
use_bambu_cli = False
try:
    use_bambu_cli = bool(int(cfg.get("use_bambu_cli", "0")))
except Exception:
    use_bambu_cli = False

if use_bambu_cli:
    from bambu_cli import run_bambu_cli_slice, bambu_cli_support_diff_stats
    # ... 调用 run_bambu_cli_slice(model_path, ...)
```

## 已知风险 & 缓解

| 风险 | 检查点 | 缓解 |
|------|--------|------|
| libslic3r 内部用 wxString | 编译报错 `wx/string.h not found` | 用 `#ifdef __WX__` guard + std::string 替代 |
| Print API 签名不同 | `Print::apply()` 调用报错 | 对照片源码调整参数 |
| Config 键名差异 | 切片时 config 校验失败 | 用现有 Bambu JSON 对照测试 |
| 构建依赖缺失 | cmake 报错找不到 Boost 等 | Dockerfile 已包含全部依赖 |

## CLI 使用示例

```bash
# 基础切片
bambu_cli --output model.gcode model.stl

# 指定 Bambu JSON profiles
bambu_cli \
  --printer profiles/bambu/machine.json \
  --process profiles/bambu/process.json \
  --filament profiles/bambu/filament.json \
  --output model.gcode \
  --stats \
  model.stl

# 覆盖参数
bambu_cli \
  --layer-height 0.16 \
  --infill 15 \
  --set perimeters=2 \
  --output model.gcode \
  model.stl

# 导出 3MF + G-code
bambu_cli \
  --output model.gcode \
  --export-3mf model.3mf \
  model.stl
```
