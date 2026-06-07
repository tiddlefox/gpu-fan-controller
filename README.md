# GPU 外挂风扇温控系统

Arduino + Python 实现 AMD Radeon V620 Pro 涡轮风扇的闭环温控，替代固定占空比方案。

## 问题背景

- Radeon V620 Pro 涡轮显卡，风扇转速控制接口不可用（笔记本外接场景）
- 手动设 Arduino PWM 占空比 170/255 (67%)，噪音可接受但高负载过热降频
- 空载时风扇也全速转，噪音浪费

## 硬件

| 组件 | 说明 |
|------|------|
| Arduino Uno R3 (CH340 克隆) | USB 串口接收占空比，D9 输出 25kHz PWM |
| 4-pin PWM 涡轮风扇 | 12V 独立供电，PWM 信号线接 Arduino D9 |
| USB A-B 线 | Arduino 连笔记本 |

无需额外采购元件。

## 架构

```
Linux 笔记本 ──USB 串口──→ Arduino Uno ──25kHz PWM──→ 风扇 PWM 线
    │
    │  读取 /sys/class/drm/card*/device/hwmon/hwmon*/temp1_input
    │  (amdgpu 驱动暴露的 GPU edge 温度，单位毫度)
    │
    └─ Python daemon (systemd) ─→ 温度查曲线 → 占空比 0-255 → 串口发 1 字节
```

## 通信协议

**PC → Arduino**：单字节 0-255，代表 PWM 占空比。

**Arduino → PC**：回显占空比数值（文本 + 换行），用于确认。

**Arduino 端**：`loop()` 中 `Serial.read()` 接收字节 → `OCR1A = map(val, 0, 255, 0, 639)` → D9 输出 25kHz Fast PWM。3 秒无数据自动回退到安全占空比。

## 控制策略

### 迟滞（Hysteresis）

风扇 **55°C edge 启动**，**45°C edge 停转**，10°C 缓冲区防止温度在临界点附近反复启停。

### 温度-占空比曲线（edge 温度）

| Edge 温度 | 占空比 | 逻辑 |
|-----------|--------|------|
| < 45°C | 0 (停转) | 空载完全静音 |
| 55°C | 60 (24%) | 启动点 |
| 65°C | 85 (33%) | 轻度负载 |
| 75°C | 140 (55%) | 中度负载 |
| 85°C | 200 (78%) | 接近降频，快速加压 |
| > 90°C | 255 (100%) | 全速兜底 |

> V620 Pro junction 降频线 105°C，edge 85°C 对应 junction ~100°C。

### 为什么能稳定在目标温度

这是一个**负反馈闭环**：温度高于平衡点 → 风扇加速 → 温度下降；温度低于平衡点 → 风扇减速 → 温度回升。每 0.5 秒修正一次，温度被不断拉回平衡点。曲线在 80°C 附近的斜率正好使散热能力匹配典型负载的发热量，所以温度天然稳定。

## 部署

```
fan_controller/
├── fan_controller.ino      # Arduino 端
├── gpu_fan_daemon.py        # Python 守护进程
├── gpu-fan.service.example  # systemd 服务模板 (复制后修改路径和用户名)
├── 99-gpu-fan.rules.example # udev 规则模板 (复制后修改 USB 端口)
└── requirements.txt         # pyserial
```

### 安装步骤

```fish
# 1. 上传 Arduino 代码 (Arduino IDE → 选择 Uno → 上传)

# 2. 安装 udev 规则（从模板复制并修改 USB 端口）
cp 99-gpu-fan.rules.example 99-gpu-fan.rules
# 编辑 99-gpu-fan.rules，将 KERNELS=="<your-usb-port>" 替换为实际端口
# 查找端口: udevadm info --name=/dev/ttyUSB0 --attribute-walk | grep KERNELS
sudo cp 99-gpu-fan.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger

# 3. 安装 Python 依赖
pip install pyserial

# 4. 安装 systemd 服务（从模板复制并修改路径和用户名）
cp gpu-fan.service.example gpu-fan.service
# 编辑 gpu-fan.service，修改 ExecStart 路径和 User
sudo cp gpu-fan.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gpu-fan

# 5. 验证
systemctl status gpu-fan
```

### 日常操作

```fish
systemctl status gpu-fan                    # 查看状态
journalctl -u gpu-fan -f                    # 实时日志
sudo systemctl restart gpu-fan              # 重启服务
```

## 测试结果

| 场景 | Edge 温度 | 占空比 | 效果 |
|------|-----------|--------|------|
| 空载 | 32°C | 0 (停转) | 完全静音 |
| LLM 推理 (ollama qwen3.6:27b) | 73-74°C | 124-134 (49-53%) | 温度平稳，明显比旧方案安静 |
| 对比旧方案 (固定 170) | 85°C+ | 170 (67%) | 空载浪费噪音，高负载还更热 |

## 调参指南

编辑 `gpu_fan_daemon.py` 顶部配置：

- `FAN_CURVE` — 温度-占空比曲线，前密后疏 → 噪音优先；前疏后密 → 散热优先
- `HYSTERESIS_ON` / `HYSTERESIS_OFF` — 启动/停转温度，差值越大越不抖
- `INTERVAL` — 轮询间隔，0.5s 已够用，再小意义不大

## 调试技巧

```fish
# 无需 Arduino 也能测试 PC 端
python3 -c "
from gpu_fan_daemon import find_hwmon_path, identify_temp_sensor, read_temp, compute_duty
hwmon = find_hwmon_path()
tp = identify_temp_sensor(hwmon)
temp = read_temp(tp)
print(f'{temp=}, duty={compute_duty(temp)}')
"

# 监控 GPU 温度
watch -n 1 rocm-smi -t

# 串口直连测试（echo 端口用 screen）
screen /dev/ttyUSB0 115200
# 输入任意字符测试 Arduino 是否回显
```
