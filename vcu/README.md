# WS22 VCU (Python)

MARAND 모터 + WaveSculptor22를 CAN으로 구동하는 간단한 VCU 프로그램입니다.

## 실행

```
pip install python-can
python vcu_app.py --sim      # 하드웨어 없이 시뮬레이터로 먼저 확인
python vcu_app.py            # 실제 CAN
```
`tkinter`는 Windows용 Python에 기본으로 들어 있습니다.

## 파일 구성

| 파일 | 역할 |
|---|---|
| `vcu_app.py` | UI (버튼 4개, 모드/방향, 슬라이더 1개, 계측값) |
| `vcu_core.py` | CAN 송수신, 보호 로직, 로깅, 시뮬레이터 |
| `dbc_lite.py` | DBC 파서 (추가 라이브러리 필요 없음) |
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
