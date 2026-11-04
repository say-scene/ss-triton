# STT 요청 증가 시 처리 슬롯 포화로 응답 지연·503 발생 (t3.large)

> 상태: 초안 · 작성일: 2026-11-03
> 기준 구성: stt 노드 t3.large, `WHISPER_CPU_THREADS=1`, `STT_CONCURRENCY=1`, CPU 1500m(Guaranteed), `small.en`
> 알림 `SttSlotSaturated`는 이 문서와 함께 추가하는 것을 전제로 작성했다(작성 시점에는 미적용). 설정 방법은 맨 아래 **부록. 알림 설정 가이드**에 있다.
> `(확인 필요)` 표시는 Grafana 화면·실제 알림 기록으로 채워야 하는 칸이다.

---

## 1. 장애 또는 성능 저하 개요

- **상황 제목**: STT 요청 증가 시 처리 슬롯 포화로 응답 지연 및 503(`STT_BUSY`) 발생
- **발생 또는 재현 일시**: 2026-10-29 (t3.large 단일 Pod 부하 재현, `bench.py`) · 알림 발화 시각 (확인 필요)
- **대상 서비스**: `ss-triton` (CPU Whisper STT, namespace `sayscene`) → 호출 측 ss-be `/api/v1/stt`
- **영향 범위**
  - 동시 요청 2건부터 응답 시간이 약 2배(p50 6초 → 12초)로 늘었다.
  - 동시 요청 4건에서는 대기 상한(10초)을 넘긴 요청이 503 `STT_BUSY`로 거절됐다(2분간 503 22건).
  - 사용자 입장: 말하기 연습 중 음성 인식 결과가 늦게 오거나 "잠시 후 다시 시도" 오류를 받는다.
  - 다른 서비스(BE·LLM) 영향 없음. STT 전용 노드라 다른 Pod와 CPU를 다투지 않는다.

---

## 2. 이상 징후

처음 확인한 이상 상태:

- **알림 발화**: `SttSlotSaturated` — 2분간 (추론 사용시간 비율 + 큐 대기시간 비율)이 **0.8(80%)** 을 2분 연속 초과
- **응답 시간 증가**: 동시 1건 p50 6.07초 → 동시 2건 12.44초 → 동시 4건 p90 15.40초
- **오류율 증가**: 동시 4건에서 503 `STT_BUSY` 22건 (+ 서버가 본문을 읽기 전 503을 보내 끊긴 연결)
- **CPU 사용량**: 처리 중에는 항상 100%. 평상시(1건 처리)와 포화(1건 처리 + 대기 여러 건)가 CPU로는 똑같이 보여 **CPU로는 포화를 구분할 수 없었다**
- **Memory**: 피크 1.19GiB / limit 2Gi — 정상
- **Pod Restart**: 0 — 정상

첨부: Grafana 패널 ① 슬롯 사용률, ② 503 비율, ③ 대기 시간 p90 (확인 필요 — 캡처 첨부)

---

## 3. 원인 분석

### 확인한 메트릭

알림 지표(= KEDA 주 기준과 같은 식):

```
sum(rate(stt_inference_seconds_sum{namespace="sayscene"}[2m]))
+
sum(rate(stt_queue_wait_seconds_sum{namespace="sayscene"}[2m]))
```

- 앞 항 = **추론 사용시간 비율**: 2분 동안 Pod가 실제로 계산한 시간의 비율. `STT_CONCURRENCY=1`이라 Pod당 최대 1.0이다.
- 뒤 항 = **큐 대기시간 비율**: 같은 2분 동안 요청들이 슬롯을 기다린 시간의 합 ÷ 120초.
- 두 항의 합은 "서버 안에 평균 몇 건이 있었나"다(리틀의 법칙). **1.0이면 Pod 1개가 쉬는 시간 없이 꽉 찬 상태, 1.0을 넘는 부분은 줄 서 있던 요청이다.**

| 부하 | 지표 값 | 처리량 | p50 | p90 | 503 |
|---|---|---|---|---|---|
| 사용자 1명 실사용 | 0.175 | — | — | — | 0 |
| 동시 1건 연속 | 약 1.0 | 0.164 rps | 6.07s | 7.07s | 0 |
| 동시 2건 | 약 2.0 | 0.161 rps | 12.44s | 13.28s | 0 |
| 동시 4건 | 2 이상 (확인 필요) | 0.165 rps | 12.95s | 15.40s | 22 |

- 동시 요청이 늘어도 **처리량은 약 0.16 rps(분당 약 10건)로 고정**이고, 늘어난 만큼 대기시간 비율만 커졌다.
- 대기시간 p90은 503으로 빠진 요청을 집계하지 않아 503이 나기 시작하면 실제보다 낮게 보인다. 503 비율과 함께 봤다.

### 확인한 로그

- 요청마다 `stt model=... audio_s=... elapsed_s=... rtf=...` 한 줄. 처리 시간(`elapsed_s`)은 부하와 무관하게 약 6초로 일정했다 → **계산 자체가 느려진 것이 아니라 기다리는 시간이 늘어난 것**.
- 기동 로그 `Whisper 준비 완료: ... (threads=1, concurrency=1, queue_timeout=10.0s)`로 설정값 확인.
- Pod 재시작·OOM 로그 없음.

### 원인으로 판단한 내용

**Pod 1개의 처리 능력(약 0.16 rps)보다 요청이 많이 들어와 단일 처리 슬롯 앞에 대기열이 쌓였다.**
처리 약 6초 + 대기 상한 10초 구조라, 대기 요청이 2건을 넘으면 10초 안에 순서가 오지 않아 503이 된다.

### 판단 근거

1. 처리 시간(`elapsed_s`)은 일정하고 대기 시간만 증가 → 계산 성능 저하가 아니라 용량 부족.
2. t3.large의 vCPU 2개는 물리 1코어(하이퍼스레드)로 알고 있다. threads·concurrency를 (2,1)·(1,1)·(1,2)로 바꿔도 처리량이 모두 약 0.16 rps로 같았다 → **Pod 안의 설정으로는 처리량을 늘릴 수 없고 노드(코어) 수에만 비례**한다.
3. 메모리·재시작·다른 Pod 간섭은 정상 → 다른 원인 배제.
4. KEDA가 2번째 Pod를 띄우기까지 **약 3분**(감지 1~2분 추정 + 노드 기동~Ready 약 1분 45초 실측)이 걸려, 그 사이 들어온 요청은 기존 Pod의 대기열과 503으로 처리됐다.

---

## 4. 대응 과정

1. Discord로 `SttSlotSaturated` 알림 수신 (확인 필요: 발화 시각)
2. Grafana에서 ① 슬롯 사용률, ② 503 비율, ③ 대기 시간 p90 확인 → 추론 비율은 1.0에 붙어 있고 대기 비율이 증가함을 확인
3. Pod 로그 확인 → `elapsed_s` 약 6초로 일정, 재시작·OOM 없음 → 계산 저하가 아닌 대기열 포화로 판단
4. Pod 내부 설정 조정 검토 → t3.large에서 threads·concurrency 조합 (2,1)·(1,1)·(1,2) 모두 처리량 약 0.16 rps로 동일(실측). 설정으로는 처리량이 늘지 않으므로 **노드(Pod) 수를 늘리는 수평 확장으로 대응**하기로 결정
5. KEDA 확장 상태 확인 (`kubectl get scaledobject,hpa -n sayscene -w`) → HPA TARGETS가 목표(800m)를 넘어 Pod 2개·stt 노드(t3.large) 2대로 확장됨 (최대 2)
6. 2번째 Pod `Ready` 확인 (`kubectl get pod -n sayscene -l app=ss-triton -o wide`). 결정 → Ready 약 1분 45초(실측), 부하 시작 기준 약 3분
7. 같은 부하 조건에서 Grafana로 Pod별 지표·503 비율이 내려가는지 재확인 (확인 필요: 확장 후 값)
8. 부하 종료 후 300초 안정화 뒤 Pod 1개로 축소, 빈 stt 노드 회수 확인

---

## 5. 대응 결과

- **서비스 정상화 여부**: KEDA 확장으로 Pod 2개·노드 2대가 되었고, 부하를 멈춘 뒤 Pod 1개로 축소되고 빈 노드가 회수되는 것까지 확인했다(2026-10-29 KEDA 테스트). 확장 후 503이 멈췄는지는 (확인 필요).
- **개선된 지표**: 대응 전·후 비교 (구성은 동일: t3.large, threads 1, concurrency 1)

| 항목 | 대응 전 (Pod 1개) | 대응 후 (Pod 2개) |
|---|---|---|
| 전체 처리량 | 0.16 rps | 약 0.32 rps 예상 (처리량은 노드 수에 비례, 확인 필요) |
| Pod별 슬롯 사용률 (동시 2건) | 약 2.0 | 약 1.0 예상 (확인 필요) |
| 동시 2건 p50 | 12.44s | 약 6s 예상 — 대기 없이 바로 처리 (확인 필요) |
| 503 비율 | 동시 4건에서 발생 (22건 / 2분) | (확인 필요) |

- **알림 상태**: 확장이 끝나 Pod별 값이 0.8 아래로 내려가면 resolved. 부하 종료 시에도 약 2~4분 안에 resolved 예상(2분 창 + `group_wait`) (확인 필요: 실제 resolved 시각)
- **재발 여부**: (확인 필요). 구조상 두 경우에 다시 발생한다.
  - 확장이 끝나기 전 약 3분 동안 몰린 요청은 기존 Pod의 대기열과 503으로 처리된다.
  - Pod 2개(상한)로도 부족한 동시 사용자(약 0.32 rps 초과)가 들어오면 확장 후에도 계속된다.

---

## 6. 개선 및 재발 방지 방안

| 구분 | 내용 | 상태 |
|---|---|---|
| 알림 추가 | `SttSlotSaturated` (슬롯 사용률 > 0.8, 2분 지속) + 503 비율 알림. 부록 참고 | 이 문서와 함께 추가 |
| 확장 상한 | `maxReplicaCount 2`는 KEDA 테스트용 상한. 처리량이 노드 수에만 비례하므로 동시 사용자 목표(Pod당 약 0.16 rps)에 맞춰 NodePool limits와 함께 올린다 | 검토 |
| 확장 속도 | KEDA 쿼리 `rate[2m] → [1m]` 또는 threshold 0.8 → 0.73(실제로 0.8에서 확장되게) | 검토 |
| 사전 확장 | 몰리는 시간대(수업·시연)를 알면 KEDA `cron` trigger 또는 `minReplicaCount 2`로 미리 2개 유지. 확장 대기 약 3분 동안의 503을 없앤다 | 검토 |
| 대시보드 | 슬롯 사용률(추론/대기 분리), 503 비율, 대기 p90, Pod 수를 한 화면에 | 예정 |
| 운영 점검 | 배포 후 Pod 로그의 `threads=, concurrency=` 값과 HPA TARGETS 확인을 점검 항목에 추가 | 예정 |
| 가이드 | CPU 사용률이 아니라 슬롯 사용률로 포화를 판단한다는 내용을 STT 운영 매뉴얼에 반영 | 예정 |

---

## 부록. 알림 설정 가이드

### 무엇을 알리나

| 알림 | 조건 | 의미 |
|---|---|---|
| `SttSlotSaturated` | Pod별 (추론 사용시간 비율 + 큐 대기시간 비율) > 0.8, 2분 지속 | 처리 슬롯이 80% 이상 차 있음. 곧 대기·503이 시작된다 |
| `SttBusyRateHigh` | 503(`STT_BUSY`) 비율 > 5%, 2분 지속 | 이미 사용자가 거절당하고 있음 |

- **Pod별로 본다.** KEDA는 전체 합계 ÷ 0.8로 Pod 수를 정하지만, 알림을 합계로 걸면 Pod 2개가 각각 0.5씩 여유 있게 일할 때도 1.0으로 울린다.
- 임계치 0.8은 `STT_CONCURRENCY=1`(Pod당 용량 1.0) 기준이다. concurrency를 2로 바꾸면 1.6으로 바꾼다.
- 동시 1건이 쉬지 않고 들어오면 503 없이도 약 1.0이 되어 울린다. "여유 0" 경고로 의도한 동작이다.
- KEDA는 Pod 1개일 때 약 0.88부터 확장한다. 알림이 확장보다 조금 먼저 오고, 확장이 끝나면 Pod별 값이 내려가 자동 해소된다. **해소되지 않으면 최대 Pod 수(2)에 도달했거나 확장이 실패한 것**이다.

### 1) 규칙 추가

ss-k8s `platform/kube-prometheus-stack/prometheus/values.yaml`의 `additionalPrometheusRulesMap`에 기존 `vllm-latency` 아래로 추가한다.

```yaml
  # STT 처리 슬롯 포화. 값 = Pod 안 평균 요청 수(처리 + 대기). concurrency 1이면 Pod당 용량 1.0.
  # concurrency를 바꾸면 0.8도 (concurrency × 0.8)로 맞춘다.
  stt-saturation:
    groups:
      - name: stt-saturation
        rules:
          - alert: SttSlotSaturated
            expr: >-
              sum by (pod) (rate(stt_inference_seconds_sum{namespace="sayscene"}[2m]))
              + sum by (pod) (rate(stt_queue_wait_seconds_sum{namespace="sayscene"}[2m]))
              > 0.8
            for: 2m
            labels:
              severity: warning
            annotations:
              summary: "STT 처리 슬롯 포화 — 2분간 추론+대기 비율이 80%를 2분 연속 초과"
              description: |-
                슬롯 사용률 {{ $value | humanizePercentage }} (추론 {{ with query "sum(rate(stt_inference_seconds_sum{namespace=\"sayscene\"}[2m]))" }}{{ . | first | value | humanizePercentage }}{{ end }} · 대기 {{ with query "sum(rate(stt_queue_wait_seconds_sum{namespace=\"sayscene\"}[2m]))" }}{{ . | first | value | humanizePercentage }}{{ end }})
                Pod {{ with query "count(up{namespace=\"sayscene\", service=\"ss-triton\"} == 1)" }}{{ . | first | value | printf "%.0f" }}{{ end }}개 (최대 2)
                읽는 법: Pod 2개인데 계속되면 확장 상한 도달. Pod 1개로 계속되면 KEDA·노드 기동 확인
          - alert: SttBusyRateHigh
            expr: >-
              sum(rate(stt_requests_total{namespace="sayscene", result="busy"}[2m]))
              / (sum(rate(stt_requests_total{namespace="sayscene"}[2m])) > 0)
              > 0.05
            for: 2m
            labels:
              severity: warning
            annotations:
              summary: "STT 503(STT_BUSY) 비율 5% 초과 — 대기 10초를 넘긴 요청 거절 중"
              description: "503 비율 {{ $value | humanizePercentage }}"
```

- `{namespace="sayscene"}`가 붙으므로 Alertmanager에서 **마지막 기본 경로(평일 09:00~19:00, Discord)** 로 간다. 별도 route 추가는 필요 없다.
- `service="ss-triton"` 라벨은 ServiceMonitor 설정상 붙는 것으로 예상했다. 적용 전 Prometheus에서 `up{namespace="sayscene"}`로 라벨 이름을 확인한다 (확인 필요).

### 2) 적용

```bash
# ss-k8s에 커밋·푸시 후 Argo CD에서 kube-prometheus-stack Sync
kubectl get prometheusrule -n monitoring | grep stt-saturation
```

Prometheus UI → Alerts에 `SttSlotSaturated`, `SttBusyRateHigh`가 `inactive`로 보이면 로드 완료.

### 3) 동작 확인

```bash
# control plane에서 동시 2건 5분 부하
python3 bench.py -c 2 -d 300
```

예상 흐름:

| 시점 | 상태 |
|---|---|
| 부하 시작 후 약 1분 | 2분 창 평균이 0.8을 넘어 `pending` |
| +2분 (`for`) | `firing` |
| +30초 (`group_wait`) | Discord 도착 (부하 시작 후 약 3분 30초) |
| KEDA 확장 완료 후 | Pod별 값이 0.8 아래로 내려가 `resolved` |

- 업무 시간 외에 발화하면 버려지지 않고 다음 평일 09:00에 도착한다.
- 메시지 값은 2분 평균이다. 알림이 늦게 오므로 순간값보다 실제 상태에 가깝다.

### 4) Grafana 패널

```
# ① 슬롯 사용률 (Pod별, 추론/대기 분리해서 쌓은 막대로 보면 원인이 바로 보인다)
sum by (pod) (rate(stt_inference_seconds_sum{namespace="sayscene"}[2m]))
sum by (pod) (rate(stt_queue_wait_seconds_sum{namespace="sayscene"}[2m]))

# ② 503 비율
(sum(rate(stt_requests_total{namespace="sayscene", result="busy"}[2m]))
 / (sum(rate(stt_requests_total{namespace="sayscene"}[2m])) > 0)) or vector(0)

# ③ 대기 시간 p90
histogram_quantile(0.9, sum by (le) (rate(stt_queue_wait_seconds_bucket{namespace="sayscene"}[2m]))) or vector(0)
```

임계선: ①에 0.8, ②에 0.05.

### 롤백

규칙 블록(`stt-saturation`)을 지우고 Sync한다. 알림만 사라지며 KEDA 확장·서비스 동작에는 영향이 없다.
