# 澄湾设备厂人工校验采集台

班长根据生产工单登记当天清册，操作员按道采集设备原码，质检员按冻结口径判定，晚班凭回执核对原件。终端使用一台模拟仪器和一个实际选择器。已发读单保留发读瞬间的物理档道，操作员等待期间可以继续选位。

## 运行

使用 Python 3.11 标准库和 SQLite。在项目根目录执行。标准回归入口由运行平台提供。

```sh
python3 -B check_project.py
python3 -B demo.py
```

直接运行完整 unittest 套件可用 `python3 -B -m unittest discover -s tests -t . -v`。演示使用独立临时库，显式装载 `samples/day_shift.json`，执行实际选位、发读、迟回、换算、签发和停源重开。任何断言失败均使进程以非零状态退出。

## 一班的工作

1. 班长把生产工单的有序档道、单位、比例、偏置及限值登记为一个冻结班次。
2. 班长可以用同号同参重开。改第三道、改变声明顺序或改变任一口径均触发冲突，原清册继续有效。
3. 操作员选 A 并按下一道，到 A/2 后发出独立读单。设备暂未返回时，领取显示待回。
4. 操作员换到 B/1 准备下一点。A/2 的原码 6 迟回后仍使用 A/2 口径，得到 `6 × 1/3 + 1 = 3 kPa`，恰在闭区间 `[1, 3]` 的上端，签发通过件。
5. 操作员再在 A/2 发读，原码 7 得 `10/3 kPa`，也签发完整回执，结论为超限，供质检员复核。
6. 取消的读单释放在读位，原单迟回不会签发。提交失败时用原单重试，已核验的原码仍可用。
7. 停止设备并重开档案，晚班按号核对原单、实际位置、原值、原口径和当时的判定。
8. 晚班按班取出实际签发顺序的全量记录。已登记空班返回空清单。

## Python 接口

- `ArchiveStore.open(path)` 打开或初始化档案，支持 `with` 和显式 `close()`。重复初始化保留已登记清册和已签件。`open_shift_from_manifest(store, path)` 显式登记 JSON 清册。`open_shift(store, shift_id, catalog, calibrations)` 接受由 `make_lane_catalog` 构造的清册和以 `Position` 为键的逐位 `CalibrationProfile`。按声明顺序比较清册，按精确数值比较口径。有效异参抛 `ShiftConflictError`，非法输入抛 `ValueError`。
- JSON 清册由 `shift_id` 和有序 `profiles` 数组组成。每档有 `name` 和有序 `lanes`，每道有 `lane` 和 `calibration`。档名及班号为非空文本，道号为整数或非空文本。口径含 `unit`、`scale`、`offset`、`lower`、`upper`，可附 `offset_unit`、`lower_unit`、`upper_unit` 以显式声明相同单位。整数可直接输入，十进制和分数写成字符串。Python 配置也接受 `Fraction`。档道和数值有效时才持久登记。
- 以 `SimulatedDevice(config.catalog)` 创建仪器，再以 `ManualTerminal(device, config)` 建立班次会话。`select("A")` 进行一次实际换档并进入首道；`select()` 在实际当前档内前进。`current_position()` 取得实际位置，`describe_selection(terminal)` 返回班号、档道及从 1 起的道序。无班次、无当前位置时前进、未知档或越界均抛 `SelectionError`。
- `Readout(terminal, store)` 为终端建立唯一的读单工作流。`begin_read()` 返回带 `order_id` 和 `position` 的读单快照。设备发读参数只有读单号。`device.deliver_reply(order_id, raw)` 投递携带原锁存证据的回包，便于确定性演示和回归。`finish_read(order_id)` 领取对应回包，返回 `ReadStage.PENDING` 或 `Receipt`。`finish_read(order_id, reply)` 可直接转交一个 `DeviceReply`。无参数回值重试优先使用已验原包，有参数重试会重新核对证据和原载荷。读单守卫抛 `ReadOrderError`，非法回包抛 `ValueError` 或其子类。`cancel_read(order_id)` 返回 `ReadStage.CANCELLED`。停用后的设备操作抛 `RuntimeError`。终端和在读状态在同一事件循环中按调用顺序处理。
- 原码只接受整数、十进制文本或分数文本。没有回包与收到 `None` 载荷分别代表待回和非法回包。使用 `Fraction` 完成计算和比较。规范值及口径使用同一单位，上下限均包含在通过区间，等值限值也可作为单点区间。写入失败抛 SQLite 错误，原单可重试。
- `read_receipt(store, receipt_id)` 与 `export_shift(store, shift_id)` 使用同一固定回执表示，读取过程无需设备。未知回执或班次抛 `LookupError`。数据库持久保存已签件和清册。

已签回执的 `shift_id`、`order_id`、`position`、`raw` 和 `measurement` 可直接用于核对。`measurement` 包含精确规范量、判定和口径快照。
