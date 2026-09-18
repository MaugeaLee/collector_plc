# collector-plc

Edge PLC 수집기. `devices[]`마다 워커 스레드로 PLC를 주기 스캔하고 ZeroMQ PUB으로 게이트웨이에 전달한다.

---

## Scan SSOT (타 프로세스 계약)

주기 스캔 목록의 **단일 진실 공급원(SSOT)** 은 `ScanPoint` (`addr` + `type`) 이다.  
스키마 정의: `model/scan_model.py`, 와이어 DTO: `model/protocol_model.py`.

### 두 포맷

| 역할 | `plc_setting.json` | `cmd_r` SET_SCAN body |
|------|--------------------|------------------------|
| **신규 SSOT** (권장) | `scan: [{ "addr", "type" }, …]` | `scan: [{ "addr", "type" }, …]` |
| **레거시** | `scan_addresses: ["D100", …]` | `d_address: ["D100", …]` |

**우선순위:** `scan`이 비어 있지 않으면 `scan`만 사용. 없으면 레거시 목록을 word|bit로 승격.

| 레거시 주소 | 승격 `type` |
|-------------|-------------|
| `D800.0` / `D800.A` (워드.비트) | `bit` |
| `R3000` (단일 워드) | `word` |
| `D100~101` 등 구간 | **불가** → `scan` 사용 |

`SET_SCAN`은 `scan`만 갱신한다(`scan_addresses`를 만들거나 미러링하지 않음).  
`d_address`만 오면 word|bit로 `scan`에 승격한다. `scan_addresses`는 `plc_setting.json`에 키가 남아 있을 때의 읽기 폴백만.

### ScanPoint

```json
{ "addr": "D100", "type": "word" }
```

| `type` | `addr` 제약 | 값 |
|--------|-------------|-----|
| `word` | 단일 워드 (`D100`) | `int` (16bit) |
| `bit` | 워드.비트 단일 (`D100.0`, `D800.A`) | `0` / `1` |
| `int` | **2워드 이상 구간** (`D100~101`) | 다중 워드 정수 (엔디안: 디바이스 `word_endian`) |
| `long word` | **정확히 2워드** 구간 (`D100~101`) | DWORD 정수 (`int`와 동일 디코드) |
| `ascii` | 비트 불가, 1워드 이상 구간 또는 단일 (`D100~103`) | 문자열 |

구간 표기: `D100~200`, `D100~D200`, `ZR10~ZR20` (`~` 앞뒤 공백 허용). 시작·끝 디바이스 코드가 다르면 거부.

> **구현 범위:** `int` / `long word` / `ascii`는 XGT·MC·Modbus `scan` 경로에서 읽기 지원.

### 스캔 목록이 오는 경로

1. **부팅** — `plc_setting.json`의 `devices[].scan` 또는 `scan_addresses` (+ `scan_period_ms`)
2. **런타임** — 상위(게이트웨이) → ZeroMQ `cmd_r` `SET_SCAN`으로 덮어씀 (재연결 없음)

로컬 설정만으로도 동작한다. 상위가 동적으로 바꾸려면 `SET_SCAN`을 보낸다.

### ZeroMQ 토픽·프레임

- 토픽: `collector.plc.{devices[].device_key}.{msg_type}`  
  예: `collector.plc.plc-mitsubishi-1.cmd_r` / `.data` / `.ack`
- multipart: `[topic_utf8, header_json_utf8]`
- `ProtocolHeaderDTO`: `msg_id`, `gateway_address`, `collector_address`(= `device_key`), `msg_type`, `msg_body`, `timestamp_ms`

### `cmd_r` SET_SCAN

`msg_type`: `"cmd_r"`  
`msg_body` (`MsgCmdRDTO`):

| 필드 | 필수 | 설명 |
|------|------|------|
| `device_key` | ✅ | 헤더 `collector_address`와 **동일**해야 함 |
| `action` | ✅ | `"SET_SCAN"` |
| `timeout_ms` | ✅ | 수신 시각 기준 허용 소요(ms) |
| `scan` | △ | 신규 SSOT. 있으면 `d_address`보다 우선 |
| `d_address` | △ | 레거시 주소 문자열 목록 |
| `period_ms` | △ | 스캔 주기(ms). 생략 시 기존 유지 |
| `device_setup` | — | SET_SCAN에서는 사용 안 함 |

`scan` / `d_address` / `period_ms` 중 **최소 하나** 필요. 없으면 ACK `E-4105`.

#### 권장 예 (SSOT)

```json
{
  "msg_id": "…",
  "gateway_address": "gateway",
  "collector_address": "plc-mitsubishi-1",
  "msg_type": "cmd_r",
  "timestamp_ms": 0,
  "msg_body": {
    "device_key": "plc-mitsubishi-1",
    "action": "SET_SCAN",
    "timeout_ms": 3000,
    "period_ms": 1000,
    "scan": [
      { "addr": "R3000", "type": "word" },
      { "addr": "D800.0", "type": "bit" },
      { "addr": "D100~101", "type": "int" }
    ]
  }
}
```

#### 레거시 예

```json
{
  "device_key": "plc-mitsubishi-1",
  "action": "SET_SCAN",
  "timeout_ms": 3000,
  "d_address": ["R3000", "D800.0"],
  "period_ms": 1000
}
```

### `plc_setting.json` 예

```json
{
  "device_key": "plc-ls-xgt-1",
  "mode": "xgt",
  "scan_period_ms": 1000,
  "word_endian": "le",
  "scan": [
    { "addr": "D490000", "type": "word" },
    { "addr": "D490000.0", "type": "bit" },
    { "addr": "D490100~490101", "type": "int" }
  ]
}
```

레거시만 쓸 때:

```json
"scan_addresses": ["D1600", "R3000", "D1600.0"]
```

### PUB `data` 샘플

주기 스캔 결과는 `msg_type: "data"`, body `MsgDataDTO`:

```json
{
  "device_key": "plc-mitsubishi-1",
  "sample_ms": 0,
  "samples": [
    { "addr": "R3000", "type": "word", "value": 12, "error": null },
    { "addr": "D800.0", "type": "bit", "value": 1, "error": null },
    { "addr": "D510300", "type": "word", "value": null, "error": "E-2203" }
  ]
}
```

번지 단위 실패는 soft-fail (`value: null` + `error` 코드). 스캔 루프는 계속된다.

### 관련 `cmd_r` action

| action | 상태 | 비고 |
|--------|------|------|
| `SET_SCAN` | 구현 | 스캔 목록·주기만 (재연결 없음) |
| `SET_DEVICE` | 구현 | `device_setup` 전체 교체 + 재연결 |
| `READ_ONCE` / `STOP` | 미구현 | ACK `E-4102` |

### 구현 참조

| 파일 | 역할 |
|------|------|
| `model/scan_model.py` | `ScanPoint`, `ScanPointType`, `resolve_scan_points` |
| `model/protocol_model.py` | `MsgCmdRDTO`, `MsgDataDTO`, `MsgSampleDTO` |
| `model/client_model.py` | 디바이스의 `scan` / `scan_addresses` |
| `daemon.py` | `_apply_set_scan`, `device_scan_points` |
| `client/addr.py` | 단일·비트·구간 파싱 |

---

## 주소 문자열 파싱

스캔·쓰기에 쓰는 주소는 `client/addr.py`의 `parse_plc_addr()`으로 분해한다.

### 형식

```text
{디바이스}{번호}[.{비트}]
{디바이스}{시작}~[{디바이스}]{끝}   ← 구간 (Scan SSOT의 int/ascii)
```

| 예 | 디바이스 | 번호 | 비트 |
|----|----------|------|------|
| `D100` | D | 100 | (워드 전체) |
| `D100.5` | D | 100 | 5 |
| `D800.A` | D | 800 | 10 (16진 A) |
| `R3000` | R | 3000 | (워드 전체) |
| `ZR10` | ZR | 10 | (MC 전용) |
| `D100~101` | D | 100–101 | (구간, `scan` 전용) |

### 워드.비트 (소수점) 규칙 — 미쓰비시 GX Works 호환

16비트 워드 안의 비트를 가리킬 때 점(`.`) 뒤에 인덱스를 붙인다.

| 표기 | 비트 인덱스 |
|------|-------------|
| `.0` ~ `.9` | 0 ~ 9 (10진) |
| `.A` ~ `.F` | 10 ~ 15 (16진, 대소문자 무관) |

동작:

1. **MC (`mode: mc`)** — `McClient.read_device()`가 워드를 읽은 뒤 해당 비트만 0/1로 반환한다.
2. **Modbus (`mode: modbus_tcp` / `modbus_rtu`)** — D/R 워드를 읽고 동일하게 비트를 추출한다.

비트 인덱스는 항상 **0–15**이다. 범위 밖이면 `E-4001(UNSUPPORTED_ADDR)`.

### 프로토콜별 지원 디바이스

| mode | 지원 | 비고 |
|------|------|------|
| `mc` | D, W, R, ZR, SD, SW, Z (워드), M, X, Y, B, L, F, SM (비트) | pymcprotocol에 문자열 그대로 전달 |
| `modbus_tcp` / `modbus_rtu` | **D**, **R** (워드·워드.비트) | 아래 Modbus 매핑 필요 |

---

## Modbus (LS XG 등) — D와 R 분리 매핑

Modbus TCP/RTU는 PLC **내부 디바이스 이름**을 Modbus가 모른다.  
collector는 주소 **접두사(D vs R)** 로 분기하고, `plc_setting.json`의 origin과 XG5000 Modbus Settings를 **1:1로 맞춘다**.

### 변환 알고리즘 (`client/modbus_map.py`)

```text
D{n}  →  FC03/04 (d_register_type),  index = n - modbus_d_register_start
R{n}  →  FC03/04 (r_register_type),  index = n - modbus_r_register_start
```

- **D는 D로, R은 R로만** 변환한다. `D3000`과 `R3000`은 서로 다른 origin·함수 영역을 쓴다.
- `index`는 pymodbus 기준 **0-based** (Modbus 40001 = index 0).
- `modbus_r_register_start`이 `null`이면 R 주소는 `E-4001` (의도적 — 매핑 미설정).

### 설정 필드 (`modbus_tcp` / `modbus_rtu` 디바이스)

| 필드 | 기본값 | 설명 |
|------|--------|------|
| `modbus_d_register_start` | `0` | 이 D 번호 = Modbus index 0 |
| `modbus_d_register_type` | `"holding"` | D 영역 FC03(`holding`) 또는 FC04(`input`) |
| `modbus_r_register_start` | `null` | R 미사용. 설정 시 R 영역 활성 |
| `modbus_r_register_type` | `"input"` | R 영역 FC (보통 Word Read → `input`) |

### XG5000 — D와 R **동시에** Modbus로 노출하는 설정

LS XG Modbus 서버는 워드가 **두 갈래**다. D(`%MW`)와 R(`%R`)은 PLC 내부 메모리 종류가 달라 **Word Write 한 칸에 같이 넣을 수 없다.**  
→ **D는 Word Write, R은 Word Read** 로 나눠 Modbus에 올린다.

```text
  XG5000 Modbus Server
  ┌─────────────────────┐    ┌─────────────────────┐
  │ Word Write Area     │    │ Word Read Area      │
  │ 시작 %MW{d_min}     │    │ 시작 %R{r_min}      │
  └──────────┬──────────┘    └──────────┬──────────┘
             ▼                          ▼
      FC03 Holding (4xxxx)      FC04 Input (3xxxx)
      index 0 = D{d_min}         index 0 = R{r_min}
```

**겹치지 않는 이유:** 4xxxx(Holding)와 3xxxx(Input)은 Modbus **주소 공간이 다르다.**  
둘 다 index 0부터여도 `D1600`과 `R3000`이 서로 덮어쓰지 않는다.

#### XG5000에 넣을 값 (예: D1600~, R3000~ 스캔)

| XG5000 항목 | 입력 | 블록 크기 |
|-------------|------|-----------|
| **Word Write Area Address** | `%MW1600` | (스캔 D 최대 − 1600 + 1) 이상 |
| **Word Read Area Address** | `%R3000` | (스캔 R 최대 − 3000 + 1) 이상 |

시작 주소 = **스캔 번지 중 가장 작은 D/R 번호.** `%MW1600` ↔ `D1600` (번호 동일).

#### collector `plc_setting.json` (XG 숫자와 1:1)

```json
"modbus_d_register_start": 1600,
"modbus_d_register_type": "holding",
"modbus_r_register_start": 3000,
"modbus_r_register_type": "input",
"scan_addresses": ["D1600", "D1650", "R3000", "R3008"]
```

| scan | 계산 | Modbus |
|------|------|--------|
| `D1600` | 1600 − 1600 = 0 | FC03 → 40001 |
| `D1650` | 50 | FC03 → 40051 |
| `R3000` | 3000 − 3000 = 0 | FC04 → 30001 |
| `R3008` | 8 | FC04 → 30009 |

`D` 접두사 → FC03만, `R` 접두사 → FC04만. **D가 R 영역으로 섞이지 않는다.**

#### R만 스캔할 때 (`plc-ls-rtu-1`)

D가 없으면 Word Write Area는 XG에서 최소로 두고, collector는 R만 설정:

```json
"modbus_r_register_start": 3000,
"modbus_r_register_type": "input",
"scan_addresses": ["R3000", "R3008", "R3010"]
```

#### FC03(Holding)만 써야 할 때

LS Modbus 서버는 Word Write **블록 1개**만 Holding에 매핑한다. **D와 R을 동시에 FC03 하나에 넣는 설정은 불가**에 가깝다.

- R도 FC03으로 읽어야 하면: ladder로 `%R` → `%MW` 복사 후 D(MW)만 Modbus 노출  
- 또는 Word Write를 `%R3000`으로만 열어 R만 Holding (이 경우 D는 Modbus 불가)

### 설정 예 (`plc_setting.example.json` RTU)

```json
{
  "modbus_d_register_start": 1600,
  "modbus_d_register_type": "holding",
  "modbus_r_register_start": 3000,
  "modbus_r_register_type": "input",
  "scan_addresses": ["D1600", "R3000", "D1600.0", "R3000.A"]
}
```

---

## MC (미쓰비시) — 대형 D 번지 (예: D510300, D101208)

데이터시트에 `D510300`, `D101208`처럼 **Q CPU 일반 D 범위(약 D0–D12287)를 넘는 번호**가 적혀 있고, PLC에 직접 접속해 확인할 수 없을 때 아래를 순서대로 검토한다.

### 1. 데이터시트 표기가 “순수 D 디바이스”가 아닐 수 있음

- HMI/SCADA **태그 ID**, **주소 코드**, **간접指定**가 `D` 접두사 없이 붙었을 수 있다.
- 다른 벤더 표기를 미쓰비시 `D`로 옮겨 적었을 수 있다.
- **조치**: 데이터시트 작성처에 “MC Protocol 디바이스 코드 + 번호” 확인 요청.

### 2. 다른 디바이스 코드일 가능성

| MC 코드 | 용도 |
|---------|------|
| `D` | 데이터 레지스터 |
| `R` / `ZR` | 파일·링크 레지스터 |
| `W` | 링크 레지스터 (워드) |
| `SD` | 특수 레지스터 |

510300 같은 큰 숫자는 **파일 레지스터(R/ZR)** 이거나 **모듈/버퍼** 주소일 수 있다.  
**조치**: `R510300`, `ZR…` 등으로 MC 읽기 시도(현장 접속 가능해지면).

### 3. PLC 응답 `0x4031` (E-2203)의 의미

collector는 MC `0x4031`을 `E-2203(ADDR_OUT_OF_RANGE)`로 처리한다.  
**프로토콜·파싱 오류가 아니라 PLC가 “그 D 번호는 없다”고 답한 것**이다.

현재 동작:

- 해당 번지만 `value: null`, `error: "E-2203"` — **스캔 전체는 계속**된다.
- `D800.x`, `D000901`처럼 유효한 번지는 정상 수집된다.

### 4. PLC 확인 없이 할 수 있는 것

| 방법 | 설명 |
|------|------|
| **soft-fail 유지** | 잘못된 번지는 E-2203으로 남기고, 유효 번지만 게이트웨이에 전달 (현재 동작) |
| **scan_addresses 분리** | 확실한 번지(`D800.*`, `D000901` 등)만 먼저 운영, 510xxx는 별도 디바이스/플래그로 보류 |
| **plctype 확인** | `plc_setting.json`의 `plctype`(Q/L/iQ-R 등)이 실제 CPU와 일치하는지 |
| **접속 가능해지면 spot check** | GX Works 또는 MC Protocol로 `D510300` 1워드 batch read → 0x4031이면 번지 자체 무효 |

### 5. 접속 가능해질 때 확인 체크리스트

1. CPU 모듈 사양상 D 최대点数
2. 데이터시트 “D510300”이 **디바이스+番号**인지 **タグ名**인지
3. `batchread_wordunits(headdevice="D510300")` 단독 테스트
4. 실패 시 `R` / `ZR` / `W` 동일 번호 시도
5. GOT/HMI 프로젝트에서 동 주소의 **実デバイス** 확인

---

## 관련 소스

| 파일 | 역할 |
|------|------|
| `client/addr.py` | 주소 파싱, 워드.비트(0–9, A–F), 구간 |
| `client/modbus_map.py` | Modbus D/R → index 변환 |
| `client/mc_client.py` | MC Protocol 디바이스 읽기/쓰기 |
| `daemon.py` | 스캔·`cmd_r`·`read_addr` / `write_addr` |
| `model/client_model.py` | `ModbusMapSettings`, 디바이스 스키마 |
| `model/scan_model.py` | Scan SSOT (`ScanPoint`) |
| `model/protocol_model.py` | `cmd_r` / `data` 와이어 DTO |

---

## 실행

```bash
cp plc_setting.example.json plc_setting.json
# .env 설정 후
python main.py
```

자세한 프로젝트 레이아웃·ZeroMQ 토픽은 `.cursor/rules/project-conventions.mdc`, `zeromq-conventions.mdc` 참고.
