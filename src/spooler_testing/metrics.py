"""
성능 메트릭 수집 시스템 — Prometheus 스타일 실시간 메트릭.

다양한 성능 지표를 구조적으로 수집하고 시계열 데이터로 저장한다.
백분위 계산, 트렌드 분석, 리소스 사용률 추적을 지원한다.
"""

import logging
import statistics
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

import psutil

logger = logging.getLogger(__name__)


@dataclass
class MetricPoint:
    """단일 메트릭 포인트 (시간 + 값)"""
    timestamp: float
    value: float
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class PerformanceSummary:
    """성능 요약 통계"""
    count: int
    total: float
    mean: float
    median: float
    p90: float
    p99: float
    min_value: float
    max_value: float
    std_dev: float


class PerformanceMetrics:
    """
    실시간 성능 메트릭 수집 및 분석.

    Prometheus 스타일의 메트릭 타입 지원:
    - Counter: 누적 증가값 (파일 개수, 바이트 수)
    - Gauge: 현재 상태값 (메모리, CPU, 큐 깊이)
    - Histogram: 분포값 (지연시간, 파일 크기)
    """

    def __init__(self, max_history_points: int = 10000):
        self.max_history_points = max_history_points
        self._lock = threading.RLock()

        # 메트릭 저장소
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = defaultdict(float)
        self._histograms: dict[str, list[float]] = defaultdict(list)

        # 시계열 데이터 (원형 큐로 메모리 효율성)
        self._timeseries: dict[str, deque] = defaultdict(lambda: deque(maxlen=max_history_points))

        # 리소스 모니터링
        self._process = psutil.Process()
        self._system_metrics_enabled = True

        # 시작 시간
        self._start_time = time.time()

        logger.info("성능 메트릭 수집 시스템 초기화 완료 (최대 히스토리: %d개 포인트)", max_history_points)

    def increment_counter(self, name: str, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        """카운터 증가 (누적값)"""
        with self._lock:
            self._counters[name] += value
            self._record_timeseries(f"counter_{name}", self._counters[name], labels)

    def set_gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """게이지 설정 (현재 상태값)"""
        with self._lock:
            self._gauges[name] = value
            self._record_timeseries(f"gauge_{name}", value, labels)

    def record_histogram(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """히스토그램 기록 (분포값)"""
        with self._lock:
            self._histograms[name].append(value)
            # 메모리 사용량 제한 (최신 N개만 유지)
            if len(self._histograms[name]) > self.max_history_points:
                self._histograms[name] = self._histograms[name][-self.max_history_points//2:]

            self._record_timeseries(f"histogram_{name}", value, labels)

    def _record_timeseries(self, metric_name: str, value: float, labels: dict[str, str] | None) -> None:
        """시계열 데이터 기록"""
        point = MetricPoint(
            timestamp=time.time(),
            value=value,
            labels=labels or {}
        )
        self._timeseries[metric_name].append(point)

    def collect_system_metrics(self) -> None:
        """시스템 리소스 메트릭 수집"""
        if not self._system_metrics_enabled:
            return

        try:
            # 메모리 사용량 (MB)
            memory_info = self._process.memory_info()
            self.set_gauge("memory_rss_mb", memory_info.rss / 1024 / 1024)
            self.set_gauge("memory_vms_mb", memory_info.vms / 1024 / 1024)

            # CPU 사용률 (%)
            cpu_percent = self._process.cpu_percent()
            self.set_gauge("cpu_percent", cpu_percent)

            # 열린 파일 핸들 수
            num_fds = self._process.num_fds() if hasattr(self._process, 'num_fds') else 0
            self.set_gauge("open_file_handles", num_fds)

            # 스레드 수
            num_threads = self._process.num_threads()
            self.set_gauge("thread_count", num_threads)

        except Exception as e:
            logger.debug("시스템 메트릭 수집 오류: %s", e)

    def get_counter_value(self, name: str) -> float:
        """카운터 현재값 조회"""
        with self._lock:
            return self._counters[name]

    def get_gauge_value(self, name: str) -> float:
        """게이지 현재값 조회"""
        with self._lock:
            return self._gauges[name]

    def get_histogram_summary(self, name: str) -> PerformanceSummary | None:
        """히스토그램 요약 통계 계산"""
        with self._lock:
            values = self._histograms[name]
            if not values:
                return None

            sorted_values = sorted(values)
            count = len(sorted_values)

            return PerformanceSummary(
                count=count,
                total=sum(sorted_values),
                mean=statistics.mean(sorted_values),
                median=statistics.median(sorted_values),
                p90=sorted_values[int(count * 0.90)] if count > 0 else 0,
                p99=sorted_values[int(count * 0.99)] if count > 0 else 0,
                min_value=min(sorted_values),
                max_value=max(sorted_values),
                std_dev=statistics.stdev(sorted_values) if count > 1 else 0
            )

    def get_timeseries_data(self, metric_name: str,
                           last_n_seconds: float | None = None) -> list[MetricPoint]:
        """시계열 데이터 조회"""
        with self._lock:
            data = self._timeseries[metric_name]
            if not data:
                return []

            if last_n_seconds is None:
                return list(data)

            cutoff_time = time.time() - last_n_seconds
            return [point for point in data if point.timestamp >= cutoff_time]

    def calculate_rate_per_second(self, counter_name: str, window_seconds: float = 60.0) -> float:
        """카운터의 변화율 계산 (초당)"""
        timeseries_name = f"counter_{counter_name}"
        data = self.get_timeseries_data(timeseries_name, window_seconds)

        if len(data) < 2:
            return 0.0

        # 시간 윈도우 내 첫 번째와 마지막 포인트 비교
        first_point = data[0]
        last_point = data[-1]

        time_diff = last_point.timestamp - first_point.timestamp
        value_diff = last_point.value - first_point.value

        if time_diff <= 0:
            return 0.0

        return value_diff / time_diff

    def get_performance_report(self) -> dict[str, Any]:
        """종합 성능 리포트 생성"""
        with self._lock:
            runtime_seconds = time.time() - self._start_time

            report = {
                'runtime_info': {
                    'start_time': self._start_time,
                    'runtime_seconds': runtime_seconds,
                    'data_points_collected': sum(len(ts) for ts in self._timeseries.values())
                },
                'counters': dict(self._counters),
                'gauges': dict(self._gauges),
                'histograms': {},
                'rates': {},
                'system_metrics': {}
            }

            # 히스토그램 요약
            for name, values in self._histograms.items():
                summary = self.get_histogram_summary(name)
                if summary:
                    report['histograms'][name] = {
                        'count': summary.count,
                        'mean': round(summary.mean, 3),
                        'median': round(summary.median, 3),
                        'p90': round(summary.p90, 3),
                        'p99': round(summary.p99, 3),
                        'min': round(summary.min_value, 3),
                        'max': round(summary.max_value, 3),
                        'std_dev': round(summary.std_dev, 3)
                    }

            # 처리율 계산 (주요 카운터들)
            key_counters = ['files_processed', 'files_created', 'bytes_processed']
            for counter_name in key_counters:
                if counter_name in self._counters:
                    rate = self.calculate_rate_per_second(counter_name)
                    report['rates'][f"{counter_name}_per_second"] = round(rate, 2)

            # 최신 시스템 메트릭
            system_gauge_names = ['memory_rss_mb', 'cpu_percent', 'open_file_handles', 'thread_count']
            for gauge_name in system_gauge_names:
                if gauge_name in self._gauges:
                    report['system_metrics'][gauge_name] = round(self._gauges[gauge_name], 2)

            return report

    def reset_metrics(self) -> None:
        """모든 메트릭 초기화"""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()
            self._timeseries.clear()
            self._start_time = time.time()
            logger.info("모든 메트릭 초기화 완료")


# 전역 메트릭 수집기 (싱글톤 패턴)
_global_metrics: PerformanceMetrics | None = None


def get_metrics() -> PerformanceMetrics:
    """전역 메트릭 수집기 조회"""
    global _global_metrics
    if _global_metrics is None:
        _global_metrics = PerformanceMetrics()
    return _global_metrics


def reset_global_metrics() -> None:
    """전역 메트릭 수집기 초기화"""
    global _global_metrics
    if _global_metrics:
        _global_metrics.reset_metrics()
    else:
        _global_metrics = PerformanceMetrics()


# 고차 함수: 함수 실행 시간 측정 데코레이터
def measure_execution_time(metric_name: str, labels: dict[str, str] | None = None):
    """함수 실행 시간을 히스토그램으로 측정하는 데코레이터"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                execution_time = time.time() - start_time
                get_metrics().record_histogram(metric_name, execution_time, labels)
        return wrapper
    return decorator


# 비동기 함수용 데코레이터
def measure_async_execution_time(metric_name: str, labels: dict[str, str] | None = None):
    """비동기 함수 실행 시간을 히스토그램으로 측정하는 데코레이터"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                return result
            finally:
                execution_time = time.time() - start_time
                get_metrics().record_histogram(metric_name, execution_time, labels)
        return wrapper
    return decorator
