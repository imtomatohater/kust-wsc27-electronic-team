# WS22 VCU (Python)

MARAND 모터 + WaveSculptor22를 CAN으로 구동하는 간단한 VCU 프로그램입니다.

## 설치 (Windows, 프로그램 하나)

1. GitHub → **Actions → "VCU Windows build"** → 최신 실행 → Artifacts의 `WS22_VCU_Setup_x.y.z` 다운로드
2. 압축을 풀고 **`WS22_VCU_Setup.exe`** 실행 (관리자 권한 불필요). 설치 없이 쓰려면 `WS22_VCU.exe`만 실행해도 됨
3. 시작 메뉴 / 바탕화면의 **WS22 VCU** 실행 → 시작 창에서 설정 파일과 실행 모드(실제 CAN / 모니터링 / 시뮬레이터) 선택
4. 설정 파일과 로그는 **`문서\WS22_VCU\`** 에 있습니다 (처음 실행할 때 기본 설정이 복사됨, 이후 덮어쓰지 않음).
   시작 창의 `설정 폴더 열기`로 열어서 메모장으로 수정하세요.
5. 실제 CAN을 쓰려면 PC에 CAN 어댑터 드라이버가 설치돼 있어야 합니다 (PCAN → PEAK 드라이버)

직접 빌드: Windows에서 `vcu\packaging\build_windows.bat` (Python 3.12 필요, Inno Setup 6이 있으면 설치 파일까지 생성)

## 실행 (소스에서)

```
pip install python-can
python vcu_app.py --sim      # 하드웨어 없이 시뮬레이터로 먼저 확인
python vcu_app.py            # 실제 CAN
python vcu_app.py --monitor  # 모니터링 전용: 수신·표시·로그만, CAN 송신 없음, ARM 불가

# 24 V 파워 서플라이 벤치 테스트 (회생 OFF, 저전류, CAN 페달 입력)
python vcu_app.py --config config_bench24v.json --sim       # 가상 페달 노드로 먼저 확인
python vcu_app.py --config config_bench24v.json --monitor   # 실물: 페달·WS22 수신만 확인
python vcu_app.py --config config_bench24v.json             # 실물 구동

python -m unittest discover -s tests                        # 테스트 (vcu/ 에서)
```
`tkinter`는 Windows용 Python에 기본으로 들어 있습니다. 인자 없이 `python vcu_app.py`로 실행하면 시작 창이 뜹니다.

## 파일 구성

| 파일 | 역할 |
|---|---|
| `vcu_app.py` | UI (버튼 4개, 모드/방향, 슬라이더 1개, 계측값) |
| `vcu_core.py` | CAN 송수신, 보호 로직, 로깅, 시뮬레이터 |
| `dbc_lite.py` | DBC 파서 (추가 라이브러리 필요 없음) |
| `pedal.py` | 가속페달 처리 (범위 검사, 데드존/곡선, 이중 센서 비교, 브레이크 우선). I/O 없음 → MCU로 그대로 이식 |
| `dbc/vcu_pedal.dbc` | 페달 노드 → VCU 프레임 (기본 0x600) |
| `config_bench24v.json` | 24 V 파워 서플라이 벤치용 설정 |
| `tests/` | 페달 로직 단위 테스트 + 벤치 설정 시뮬 통합 테스트 |
| `dbc/ws22.dbc` | 받은 WS22 DBC (WS22 → VCU 텔레메트리) |
| `dbc/ws22_drive_cmd.dbc` | VCU → WS22 명령 DBC (0x501 Drive, 0x502 Power, 0x503 Reset) |
| `config.json` | CAN 어댑터, ID base, 보호 한계값 |

## 처음 연결 전에 꼭 확인할 것

1. **CAN ID base address** — 받은 DBC는 ID가 0x80~0x97입니다. Profinity에서 WS22의
   *Base address*와 *Driver controls base address*를 확인하고 `config.json → ids`에 넣으세요.
   - `ws22_base`: WS22 송신 base (DBC 그대로면 128 = 0x80, 공장 기본이면 1024 = 0x400)
   - `driver_controls_base`: WS22가 명령을 받는 base (기본 1280 = 0x500)
2. **CAN 어댑터** — `config.json → can`. 예: PCAN `"pcan"/"PCAN_USBBUS1"`, Kvaser `"kvaser"/0`,
   USB‑CAN(slcan) `"slcan"/"COM5"`. bitrate는 WS22 설정과 같게.
3. **`ws22.max_motor_current_Arms`** — Profinity에 설정한 WS22 *max motor current* (전류 100% 기준).
   **`ws22.bus_current_limit_A`** — Profinity에 설정한 WS22 *absolute bus current limit* 값.
   VCU가 보내는 버스 전류 명령(%)을 A로 바꾸는 기준입니다.
4. **`ws22.max_rpm_setpoint`** — 토크 모드에서 보내는 속도 상한(= 사실상 속도 리미터).
5. **`limits`의 전압값** — 지금은 임시값(100~150 V)입니다. 배터리 팩 최소/최대 전압에 맞게 수정.
6. Profinity로 **동시에 구동 명령을 보내지 마세요** (모니터링만은 괜찮음).

## 조작

- **ARM**: 슬라이더 0, 텔레메트리 수신 중, 전압 정상일 때만 가능
- **STOP** (Space / Esc): 전류 0 + DISARM
- **FAULT 리셋**: VCU 래치 해제 + WS22 Reset 명령(0x503) 전송
- **토크 모드 (일반 주행 = 가속 페달)**: 슬라이더 = 모터 전류 % = 토크. 음수는 회생제동 (`regen_max_pct`까지)
- **속도 모드 (크루즈)**: 슬라이더 = 목표 rpm, 전류 한계는 `speed_mode_current_pct`
- 모드/방향 변경은 DISARM + 정지 상태에서만

## 24 V 파워 서플라이 벤치 테스트 (`config_bench24v.json`)

| 항목 | 값 | 이유 |
|---|---|---|
| 전압 trip_low/min/max/trip_high | 18 / 20 / 26 / 28 V | 24 V 버스 기준 |
| `regen_max_pct` | 0 | **파워 서플라이는 회생 전류를 흡수 못함** → 버스 전압 상승 |
| `bus_i_max_drive_A` / `bus_i_trip_A` | 5 / 8 A | 서플라이 CC 한계보다 낮게 (CC 진입 → 전압 강하 → 저전압 trip) |
| `bus_current_limit_A` | 10 A | **Profinity의 WS22 absolute bus current limit과 같게 설정할 것** |
| `phase_i_trip_Arms` | 30 Arms | 무부하 벤치용 |
| `pedal.max_torque_pct` | 20 % | 페달 끝까지 밟아도 전류 명령 20 % |
| `max_rpm_setpoint` / `overspeed_trip_rpm` | 150 / 200 rpm | 24 V 무부하 최고속도보다 낮게 → 역기전력 trip 방지 |
| `motor.ke_learn_min_rpm` | 40 rpm | 낮은 속도에서도 Ke 학습 |

그래도 벤치 테스트 중에는:
- **속도 모드는 쓰지 마세요** (목표 rpm을 낮추면 WS22가 회생 제동). 페달 입력은 토크 모드 전용입니다.
- 속도 상한 도달 시 오버슈트 등으로 작은 회생이 생길 수 있으니 버스에 **블리드 저항**(예: 10 Ω / 100 W, 24 V에서 약 2.4 A)을 병렬로 다는 것을 권장합니다.
- 모터를 손이나 다이노로 돌리지 말고, 회전 중 서플라이를 끄지 마세요.
- Ke 확정 후 `ke_Vpk_per_rpm` 입력 → `max_rpm_setpoint ≈ 0.85 × 24 / (√3 × Ke)`로 재설정.

순서: `--sim` → `--monitor`(페달 전압/카운터, WS22 텔레메트리 확인) → ARM 후 페달 살짝.

## CAN 가속페달 입력

페달 노드(MCU ADC)가 아래 프레임을 **20 ms(50 Hz)** 주기로 보냅니다. ID는 `pedal.can_id`(기본 1536 = 0x600).

| 바이트 | 신호 | 내용 |
|---|---|---|
| 0–1 | `Apps1_mV` (u16, LE) | 센서 1 전압 mV (**분압 전, 센서 핀 기준 전압**으로 환산해서) |
| 2–3 | `Apps2_mV` (u16, LE) | 센서 2 mV. 센서가 하나면 0 (`dual_channel: false`) |
| 4 bit0 | `BrakeSwitch` | 1 = 브레이크 밟음 |
| 5 | `PedalCounter` (u8) | 매 프레임 +1 (255→0). 멈추면 노드 정지로 판단 |

```c
// STM32 예시 (HAL). 5 V 페달 → 분압 1/2 → 3.3 V ADC 라고 가정
uint16_t mv1 = adc1_raw * 3300 / 4095 * 2;
uint8_t d[8] = { mv1 & 0xFF, mv1 >> 8, mv2 & 0xFF, mv2 >> 8, brake ? 1 : 0, cnt++, 0, 0 };
// StdId = 0x600, DLC = 8
```

VCU 처리 순서 (`pedal.py`):
1. **수신 확인** — 카운터가 `timeout_s`(150 ms) 동안 안 바뀌면 고장
2. **범위 검사** — `fault_low_mV`(300) 미만 / `fault_high_mV`(4700) 초과 = 단선·단락. `fault_debounce_s` 동안 지속되면 고장 (그 사이에도 토크는 즉시 0)
3. **위치 계산** — `ch1_mV_released` → 0 %, `ch1_mV_full` → 100 % (역방향 센서도 가능). **실제 페달로 측정해서 넣으세요**
4. **이중 센서**(`dual_channel`) — 두 위치 차이가 `plausibility_pct` 초과면 고장, 정상이면 낮은 쪽 사용
5. **매핑** — `deadzone_pct` 이하 0, `full_pct` 이상 100 %, 사이는 `^curve_exp` (1 = 선형). × `max_torque_pct` → 전류 명령 %
6. **브레이크 우선** — 브레이크 ON → 0. 해제 후에도 페달을 `rearm_travel_pct` 아래로 뗐다 밟아야 토크 복귀

- ARM 중 페달 고장 → **즉시 차단**(IMMEDIATE) FAULT 래치
- ARM 조건에 "페달 뗀 상태·고장 없음" 추가
- 페달 고장/수신 상태는 DISARM·모니터 모드에서도 계속 표시됩니다
- UI에서 입력을 `슬라이더` / `CAN 가속페달`로 전환 (DISARM 상태에서만)
- `--sim`에서는 가상 페달 노드 패널(전압 슬라이더, 브레이크, 노드 끊기, 카운터 정지, APPS2 불일치)로 고장을 주입해 볼 수 있습니다

## 화면 가속페달

실제 페달처럼 **밟는 깊이**와 **밟는 시간**을 손으로 조작합니다. 숫자 입력은 없습니다.
페달은 발이 누를 수 있는 속도(160 %/s)로만 내려가고, 발을 떼면 리턴 스프링이 450 %/s로 되돌립니다.
그래서 살짝 툭 치기, 천천히 깊게 밟기, 일정하게 유지하기가 실제 페달처럼 구분됩니다.

| 조작 | 동작 |
|---|---|
| 페달을 누른 채 **아래로 끌기** / 위로 | 더 밟기 / 풀기 (다시 잡으면 현재 위치에서 이어서) |
| 마우스 버튼 놓기 | 발 뗌 → 스프링 복귀. **`발 고정`** 체크 시 그 위치 유지 |
| 마우스 휠 | ±3 % 미세 조정 |
| 키보드 **↑ / ↓** | 누르는 동안 밟기 / 풀기 (키를 떼도 위치 유지) |
| **X** / `발 떼기` 버튼 | 발 뗌 |
| **B** (누르고 있기) 또는 브레이크 페달 클릭 | 브레이크 |
| **Space / Esc** | STOP (전류 0, DISARM, 발 뗌) |

- 표시: 페달 각도·스프링·발 그림, 오른쪽 게이지(주황선 = 데드존, 빨간선 = full), **밟는 중 x.x s**, 직전 밟은 시간·최대 깊이
- **최근 10초** 그래프: 페달 %, 실제 송신한 토크 명령 %, rpm
- 입력 소스에 따라 역할이 바뀜:
  - **화면 페달**: 화면 페달 → `pedal.py` 처리(데드존/곡선/브레이크/`max_torque_pct`) → 토크 명령. **실물 WS22도 페달 노드 없이 구동 가능**
  - **CAN 페달 + 시뮬레이터**: 화면 페달이 가상 페달 노드를 움직임 (CAN 경로 전체 확인)
  - **CAN 페달 + 실물**: 화면 페달은 입력 불가, **실제 페달 위치를 그대로 보여줌**
- 안전: 창이 포커스를 잃으면 발 뗌. UI가 0.3 s 이상 갱신을 못하면 `화면 페달 응답 없음` → ARM 중이면 즉시 차단

## 모니터링

- `--monitor`: **아무것도 송신하지 않음**(0x501/0x502/0x503 없음), ARM 불가. WS22·페달 수신값 표시와 로그만. Profinity가 구동 중일 때 옆에서 보거나, 처음 배선 확인할 때 사용
- 화면: 버스 V/A/W, rpm, 상전류, Iq, BEMFq, 온도, Ah, 역기전력 비율, 속도 상한, Ke, 페달 위치/토크 명령 막대, APPS1/2 전압, 브레이크, 페달 상태, WS22 Limit 플래그(경고줄)
- 로그(`signals.csv`)에 페달 신호(`Apps1_mV`…), `source`, `pedal_travel_pct`, `pedal_torque_pct`, `pedal_fault` 열이 추가됩니다

## 보호 기능

### 1) 데이터시트 한계 — 설정이 넘으면 실행 자체를 거부
`vcu_core.py`의 `DATASHEET`(사용자가 바꾸는 값 아님)와 `MARGIN`으로 `config.json`을 시작할 때 검사합니다.

| 항목 | WS22 데이터시트 | 허용 상한 (config) |
|---|---|---|
| 버스 전압 (연속 최대) | 160 V | `bus_v_trip_high` ≤ 155 V |
| 모터 전류 | 100 Arms | `max_motor_current_Arms` ≤ 100, `phase_i_trip_Arms` ≤ 95 |
| 순간 버스 전류 | ±122 A | `bus_current_limit_A` ≤ 122, `bus_i_trip_A` ≤ 115.9 |
| 방열판 | 85 °C 이하 유지 (WS20 데이터시트 값, 보수 적용) | `heatsink_trip_C` ≤ 80 |
| 역기전력 (Ke 입력 시) | 선간 피크 < 버스 전압 | √3·Ke·`max_rpm_setpoint` ≤ `bus_v_min`×`bemf_cap_ratio` |

운전 중에도 전류 명령은 `0.9 × phase_i_trip / max_motor_current` 이하로 잘립니다.

### 2) 역기전력 여유 감시
`역기전력 비율 = √3 × BEMFq / Vbus` (선간 피크 / 버스 전압). 1을 넘으면 인버터 다이오드로 제어되지 않는 회생이 흐릅니다.
- Ke(V피크/rpm, 상-중성점): `motor.ke_Vpk_per_rpm`에 넣거나, 비워두면 주행 중 BEMFq/rpm으로 학습
- `bemf_warn_ratio`(85%) 경고 → `bemf_cap_ratio`(90%) 속도 상한 자동 적용 → `bemf_trip_ratio`(97%) 감속 차단
- 속도 상한 계산용 전압은 **내려갈 땐 즉시, 올라갈 땐 천천히(5 s)** 따라갑니다. 회생으로 올라간 전압 때문에 상한이 같이 올라가는 것을 막기 위해서입니다.
- 속도 상한은 0x501 속도 지령으로 걸리므로 WS22 속도 루프가 직접 제동해서 지킵니다.

### 3) FAULT 등급

| 등급 | 동작 | 해당 조건 |
|---|---|---|
| **즉시 차단** (IMMEDIATE) | 전류 0 즉시. WS22는 계속 스위칭하며 전류 0 유지 → 프리휠 | WS22 에러 플래그, 과전압, 버스/상 과전류, 과속, CAN 수신 끊김, CAN 송신 오류 |
| **감속 차단** (RAMP) | `fault_ramp_pct_per_s`로 전류를 0까지 줄임 (100%→0 약 1 s) | 저전압(ARM 중), 컨트롤러/모터 과열, 역기전력 여유 부족 |
| 제한 (FAULT 아님) | 전류 derate 또는 속도 상한 | 전압이 한계 근처일 때, 온도 derate 구간, 역기전력 경고 |

- 감속 차단 중 즉시 차단 조건이 생기면 **즉시 차단으로 격상**됩니다.
- 감속 차단이 끝나기 전(전류 > 0)에는 리셋할 수 없습니다.
- 모든 FAULT는 래치되어 사람이 `FAULT 리셋`을 눌러야 해제됩니다.

### 4) 구조적 한계
- 0x501을 20 Hz로 계속 보내므로 PC가 멈추면 WS22가 250 ms 뒤 스스로 모터를 멈춥니다.
- 이 보호는 CAN 주기(200 ms) 기준이라 **µs 단위 과전류는 WS22 자체 보호와 퓨즈가 막습니다.**
  Profinity의 WS22 설정 한계도 이 config 값과 같거나 더 낮게 맞추세요.
- **고속 회전 중 배터리 컨택터를 열면 안 됩니다.** BMS와 연동해 rpm이 높을 때 컨택터 개방을 막아야 합니다(이 VCU 밖의 일).

## 로그 (요구사항 2)

`로그 시작`을 누르면 `logs/`에 두 파일이 생깁니다.
- `YYYYMMDD_HHMMSS_signals.csv` — 10 Hz, DBC의 **모든 신호 이름이 열**로 들어감
  + VCU 상태/명령/제한율 (Excel에서 바로 그래프 가능)
- `YYYYMMDD_HHMMSS_raw.csv` — 모든 송수신 프레임 원본 (ID, 메시지 이름, 데이터 hex, RX/TX)

DBC를 바꾸면 로그 열도 자동으로 바뀝니다. 단, 보호 로직은 `BusVoltage`, `BusCurrent`,
`MotorVelocity`, `PhaseCurrentB/C`, `HeatsinkTemp`, `MotorTemp`, `Error*`, `Limit*`
신호 이름을 사용하므로 이 이름은 유지해야 합니다.
