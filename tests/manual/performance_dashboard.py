#!/usr/bin/env python3
"""
GGC S3 Spooler 실시간 성능 대시보드.

Plotly Dash를 사용하여 실시간 성능 메트릭을 시각화한다.
테스트 실행 중 브라우저에서 실시간으로 성능을 모니터링할 수 있다.

사용법:
  python performance_dashboard.py --port 8050
  브라우저: http://localhost:8050
"""

import argparse
import json
import time
import threading
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional

import dash
from dash import dcc, html, Input, Output, callback
import plotly.graph_objs as go
import plotly.express as px
import pandas as pd

# 프로젝트 모듈 import
import sys
sys.path.insert(0, 'src')

from spooler_testing.metrics import get_metrics


class PerformanceDashboard:
    """
    실시간 성능 대시보드.

    메트릭 데이터를 실시간으로 수집하고 웹 인터페이스로 표시한다.
    """

    def __init__(self, update_interval_seconds: float = 2.0, max_data_points: int = 300):
        self.update_interval = update_interval_seconds
        self.max_data_points = max_data_points

        # 시계열 데이터 저장소 (원형 큐)
        self.timestamps = deque(maxlen=max_data_points)
        self.throughput_data = deque(maxlen=max_data_points)
        self.memory_data = deque(maxlen=max_data_points)
        self.cpu_data = deque(maxlen=max_data_points)
        self.queue_depth_data = deque(maxlen=max_data_points)
        self.latency_data = deque(maxlen=max_data_points)

        # 누적 통계
        self.cumulative_stats = {
            'total_files_processed': 0,
            'total_bytes_processed': 0,
            'total_errors': 0,
            'uptime_seconds': 0,
            'peak_throughput': 0,
            'avg_latency': 0
        }

        # 대시보드 시작 시간
        self.start_time = time.time()

        # 데이터 수집 스레드
        self._collection_thread = None
        self._stop_collection = threading.Event()

        # Dash 앱 초기화
        self.app = dash.Dash(__name__)
        self.setup_layout()
        self.setup_callbacks()

    def setup_layout(self) -> None:
        """대시보드 레이아웃 설정"""
        self.app.layout = html.Div([
            # 헤더
            html.Div([
                html.H1("GGC S3 Spooler 실시간 성능 대시보드",
                       className="header-title"),
                html.Div([
                    html.Span("📊 ", style={'fontSize': '24px'}),
                    html.Span("상태: ", style={'fontWeight': 'bold'}),
                    html.Span("실행 중", id="status-indicator",
                             style={'color': 'green', 'fontWeight': 'bold'}),
                    html.Span(" | 업데이트: ", style={'marginLeft': '20px'}),
                    html.Span(id="last-update", style={'fontStyle': 'italic'})
                ], className="status-bar")
            ], className="header"),

            # 핵심 지표 카드
            html.Div([
                html.Div([
                    html.H3("총 처리 파일"),
                    html.H2(id="total-files", children="0"),
                    html.P("파일", className="unit")
                ], className="metric-card"),

                html.Div([
                    html.H3("현재 처리량"),
                    html.H2(id="current-throughput", children="0.0"),
                    html.P("파일/초", className="unit")
                ], className="metric-card"),

                html.Div([
                    html.H3("평균 지연시간"),
                    html.H2(id="avg-latency", children="0.000"),
                    html.P("초", className="unit")
                ], className="metric-card"),

                html.Div([
                    html.H3("큐 깊이"),
                    html.H2(id="queue-depth", children="0"),
                    html.P("파일", className="unit")
                ], className="metric-card"),
            ], className="metrics-grid"),

            # 차트 영역
            html.Div([
                # 처리량 차트
                html.Div([
                    dcc.Graph(id="throughput-chart")
                ], className="chart-container"),

                # 메모리/CPU 사용률 차트
                html.Div([
                    dcc.Graph(id="resource-chart")
                ], className="chart-container"),
            ], className="charts-row"),

            html.Div([
                # 지연시간 히스토그램
                html.Div([
                    dcc.Graph(id="latency-histogram")
                ], className="chart-container"),

                # 큐 깊이 및 오류율
                html.Div([
                    dcc.Graph(id="queue-errors-chart")
                ], className="chart-container"),
            ], className="charts-row"),

            # 상세 통계 테이블
            html.Div([
                html.H3("상세 성능 통계"),
                html.Div(id="detailed-stats")
            ], className="stats-section"),

            # 자동 업데이트 간격
            dcc.Interval(
                id='interval-component',
                interval=self.update_interval * 1000,  # 밀리초 단위
                n_intervals=0
            ),

            # CSS 스타일
            html.Div([
                html.Style("""
                    .header {
                        background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
                        color: white;
                        padding: 20px;
                        margin-bottom: 20px;
                        border-radius: 8px;
                    }
                    .header-title {
                        margin: 0;
                        font-size: 28px;
                    }
                    .status-bar {
                        margin-top: 10px;
                        font-size: 14px;
                    }
                    .metrics-grid {
                        display: grid;
                        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                        gap: 20px;
                        margin-bottom: 30px;
                    }
                    .metric-card {
                        background: white;
                        padding: 20px;
                        border-radius: 8px;
                        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                        text-align: center;
                    }
                    .metric-card h3 {
                        margin: 0 0 10px 0;
                        font-size: 14px;
                        color: #666;
                        text-transform: uppercase;
                    }
                    .metric-card h2 {
                        margin: 0;
                        font-size: 32px;
                        color: #333;
                    }
                    .unit {
                        margin: 5px 0 0 0;
                        font-size: 12px;
                        color: #888;
                    }
                    .charts-row {
                        display: grid;
                        grid-template-columns: 1fr 1fr;
                        gap: 20px;
                        margin-bottom: 20px;
                    }
                    .chart-container {
                        background: white;
                        padding: 20px;
                        border-radius: 8px;
                        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                    }
                    .stats-section {
                        background: white;
                        padding: 20px;
                        border-radius: 8px;
                        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                        margin-top: 20px;
                    }
                    body {
                        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                        background: #f5f5f5;
                        margin: 0;
                        padding: 20px;
                    }
                """)
            ])
        ])

    def setup_callbacks(self) -> None:
        """대시보드 콜백 설정"""

        @self.app.callback(
            [Output('total-files', 'children'),
             Output('current-throughput', 'children'),
             Output('avg-latency', 'children'),
             Output('queue-depth', 'children'),
             Output('last-update', 'children'),
             Output('throughput-chart', 'figure'),
             Output('resource-chart', 'figure'),
             Output('latency-histogram', 'figure'),
             Output('queue-errors-chart', 'figure'),
             Output('detailed-stats', 'children')],
            [Input('interval-component', 'n_intervals')]
        )
        def update_dashboard(n):
            """대시보드 전체 업데이트"""
            self.collect_current_metrics()

            # 기본 메트릭 카드
            current_time = datetime.now().strftime("%H:%M:%S")

            # 처리량 차트
            throughput_fig = self.create_throughput_chart()

            # 리소스 사용률 차트
            resource_fig = self.create_resource_chart()

            # 지연시간 히스토그램
            latency_fig = self.create_latency_histogram()

            # 큐/오류 차트
            queue_errors_fig = self.create_queue_errors_chart()

            # 상세 통계
            detailed_stats = self.create_detailed_stats_table()

            return (
                f"{self.cumulative_stats['total_files_processed']:,}",
                f"{self.throughput_data[-1] if self.throughput_data else 0:.1f}",
                f"{self.cumulative_stats['avg_latency']:.3f}",
                f"{self.queue_depth_data[-1] if self.queue_depth_data else 0:.0f}",
                current_time,
                throughput_fig,
                resource_fig,
                latency_fig,
                queue_errors_fig,
                detailed_stats
            )

    def collect_current_metrics(self) -> None:
        """현재 메트릭 수집"""
        try:
            metrics = get_metrics()
            current_time = time.time()

            # 시간 추가
            self.timestamps.append(current_time)

            # 처리량 계산 (files/second)
            throughput = metrics.calculate_rate_per_second('files_processed')
            self.throughput_data.append(throughput)

            # 리소스 사용률
            memory_mb = metrics.get_gauge_value('memory_rss_mb')
            cpu_percent = metrics.get_gauge_value('cpu_percent')
            self.memory_data.append(memory_mb)
            self.cpu_data.append(cpu_percent)

            # 큐 깊이
            queue_depth = metrics.get_gauge_value('file_queue_depth')
            self.queue_depth_data.append(queue_depth)

            # 지연시간 (transfer time 히스토그램에서 최신 값)
            latency_summary = metrics.get_histogram_summary('file_transfer_total_seconds')
            if latency_summary:
                self.latency_data.append(latency_summary.mean)
                self.cumulative_stats['avg_latency'] = latency_summary.mean
            else:
                self.latency_data.append(0)

            # 누적 통계 업데이트
            self.cumulative_stats.update({
                'total_files_processed': int(metrics.get_counter_value('files_processed')),
                'total_bytes_processed': int(metrics.get_counter_value('bytes_processed')),
                'total_errors': int(metrics.get_counter_value('files_transfer_failed')),
                'uptime_seconds': current_time - self.start_time,
                'peak_throughput': max(self.peak_throughput if hasattr(self, 'peak_throughput') else 0, throughput)
            })

        except Exception as e:
            print(f"메트릭 수집 오류: {e}")

    def create_throughput_chart(self) -> go.Figure:
        """처리량 차트 생성"""
        if not self.timestamps:
            return go.Figure()

        # 시간을 datetime으로 변환
        times = [datetime.fromtimestamp(ts) for ts in self.timestamps]

        fig = go.Figure()

        # 실시간 처리량
        fig.add_trace(go.Scatter(
            x=times,
            y=list(self.throughput_data),
            mode='lines+markers',
            name='처리량 (파일/초)',
            line=dict(color='#1f77b4', width=2),
            marker=dict(size=4)
        ))

        # 이동 평균 (최근 10개 포인트)
        if len(self.throughput_data) >= 10:
            moving_avg = []
            throughput_list = list(self.throughput_data)
            for i in range(len(throughput_list)):
                start_idx = max(0, i - 9)
                avg = sum(throughput_list[start_idx:i+1]) / (i - start_idx + 1)
                moving_avg.append(avg)

            fig.add_trace(go.Scatter(
                x=times,
                y=moving_avg,
                mode='lines',
                name='이동평균 (10포인트)',
                line=dict(color='#ff7f0e', width=1, dash='dash')
            ))

        fig.update_layout(
            title="파일 처리량 (실시간)",
            xaxis_title="시간",
            yaxis_title="파일/초",
            height=300,
            margin=dict(l=0, r=0, t=40, b=0)
        )

        return fig

    def create_resource_chart(self) -> go.Figure:
        """리소스 사용률 차트 생성"""
        if not self.timestamps:
            return go.Figure()

        times = [datetime.fromtimestamp(ts) for ts in self.timestamps]

        fig = go.Figure()

        # 메모리 사용량 (왼쪽 Y축)
        fig.add_trace(go.Scatter(
            x=times,
            y=list(self.memory_data),
            mode='lines+markers',
            name='메모리 (MB)',
            line=dict(color='#2ca02c', width=2),
            marker=dict(size=3),
            yaxis='y'
        ))

        # CPU 사용률 (오른쪽 Y축)
        fig.add_trace(go.Scatter(
            x=times,
            y=list(self.cpu_data),
            mode='lines+markers',
            name='CPU (%)',
            line=dict(color='#d62728', width=2),
            marker=dict(size=3),
            yaxis='y2'
        ))

        fig.update_layout(
            title="리소스 사용률",
            xaxis_title="시간",
            yaxis=dict(
                title="메모리 (MB)",
                side="left",
                color='#2ca02c'
            ),
            yaxis2=dict(
                title="CPU (%)",
                side="right",
                overlaying="y",
                color='#d62728'
            ),
            height=300,
            margin=dict(l=0, r=0, t=40, b=0)
        )

        return fig

    def create_latency_histogram(self) -> go.Figure:
        """지연시간 히스토그램 생성"""
        try:
            metrics = get_metrics()
            latency_summary = metrics.get_histogram_summary('file_transfer_total_seconds')

            if not latency_summary or latency_summary.count == 0:
                fig = go.Figure()
                fig.add_annotation(text="지연시간 데이터 없음",
                                 x=0.5, y=0.5,
                                 showarrow=False)
                fig.update_layout(title="지연시간 분포", height=300)
                return fig

            # 히스토그램 데이터 생성 (가상의 분포 - 실제로는 원시 데이터 필요)
            # 여기서는 요약 통계를 사용해 대략적인 분포를 시각화
            stats_data = [
                ('P50', latency_summary.p50),
                ('P90', latency_summary.p90),
                ('P99', latency_summary.p99),
                ('평균', latency_summary.mean),
                ('최대', latency_summary.max_value)
            ]

            fig = go.Figure(data=[
                go.Bar(
                    x=[stat[0] for stat in stats_data],
                    y=[stat[1] for stat in stats_data],
                    marker_color=['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd']
                )
            ])

            fig.update_layout(
                title=f"지연시간 통계 ({latency_summary.count}개 샘플)",
                xaxis_title="백분위",
                yaxis_title="지연시간 (초)",
                height=300,
                margin=dict(l=0, r=0, t=40, b=0)
            )

            return fig

        except Exception as e:
            print(f"지연시간 차트 생성 오류: {e}")
            fig = go.Figure()
            fig.add_annotation(text=f"차트 생성 오류: {e}",
                             x=0.5, y=0.5,
                             showarrow=False)
            fig.update_layout(title="지연시간 분포", height=300)
            return fig

    def create_queue_errors_chart(self) -> go.Figure:
        """큐 깊이 및 오류 차트 생성"""
        if not self.timestamps:
            return go.Figure()

        times = [datetime.fromtimestamp(ts) for ts in self.timestamps]

        fig = go.Figure()

        # 큐 깊이 (막대 차트)
        fig.add_trace(go.Bar(
            x=times,
            y=list(self.queue_depth_data),
            name='큐 깊이',
            marker_color='#17becf',
            opacity=0.7
        ))

        # 오류율 추가 (선 차트, 오른쪽 축)
        try:
            metrics = get_metrics()
            error_count = metrics.get_counter_value('files_transfer_failed')
            total_processed = metrics.get_counter_value('files_processed')
            error_rate = (error_count / total_processed * 100) if total_processed > 0 else 0

            fig.add_trace(go.Scatter(
                x=[times[-1]] if times else [],
                y=[error_rate],
                mode='markers',
                name='오류율 (%)',
                marker=dict(color='red', size=8),
                yaxis='y2'
            ))
        except:
            pass

        fig.update_layout(
            title="큐 깊이 및 오류율",
            xaxis_title="시간",
            yaxis=dict(title="큐 깊이 (파일 수)", side="left"),
            yaxis2=dict(title="오류율 (%)", side="right", overlaying="y"),
            height=300,
            margin=dict(l=0, r=0, t=40, b=0)
        )

        return fig

    def create_detailed_stats_table(self) -> html.Div:
        """상세 통계 테이블 생성"""
        try:
            metrics = get_metrics()
            performance_report = metrics.get_performance_report()

            # 테이블 데이터 구성
            stats_rows = [
                html.Tr([html.Td("업타임"), html.Td(f"{self.cumulative_stats['uptime_seconds']:.1f}초")]),
                html.Tr([html.Td("총 처리 파일"), html.Td(f"{self.cumulative_stats['total_files_processed']:,}개")]),
                html.Tr([html.Td("총 처리 데이터"), html.Td(f"{self.cumulative_stats['total_bytes_processed']/1024/1024:.1f}MB")]),
                html.Tr([html.Td("총 오류"), html.Td(f"{self.cumulative_stats['total_errors']}개")]),
                html.Tr([html.Td("평균 처리량"), html.Td(f"{self.cumulative_stats['total_files_processed']/self.cumulative_stats['uptime_seconds']:.2f}파일/초" if self.cumulative_stats['uptime_seconds'] > 0 else "0파일/초")]),
                html.Tr([html.Td("현재 메모리"), html.Td(f"{self.memory_data[-1] if self.memory_data else 0:.1f}MB")]),
                html.Tr([html.Td("현재 CPU"), html.Td(f"{self.cpu_data[-1] if self.cpu_data else 0:.1f}%")]),
            ]

            # 히스토그램 통계 추가
            if 'histograms' in performance_report:
                for hist_name, hist_stats in performance_report['histograms'].items():
                    if hist_name == 'file_transfer_total_seconds':
                        stats_rows.extend([
                            html.Tr([html.Td("평균 전송시간"), html.Td(f"{hist_stats.get('mean', 0):.3f}초")]),
                            html.Tr([html.Td("P90 전송시간"), html.Td(f"{hist_stats.get('p90', 0):.3f}초")]),
                            html.Tr([html.Td("최대 전송시간"), html.Td(f"{hist_stats.get('max', 0):.3f}초")])
                        ])

            return html.Table([
                html.Tbody(stats_rows)
            ], style={
                'width': '100%',
                'borderCollapse': 'collapse'
            })

        except Exception as e:
            return html.Div([
                html.P(f"통계 생성 오류: {e}", style={'color': 'red'})
            ])

    def start_data_collection(self) -> None:
        """백그라운드 데이터 수집 시작"""
        def collection_worker():
            while not self._stop_collection.is_set():
                self.collect_current_metrics()
                time.sleep(self.update_interval)

        self._collection_thread = threading.Thread(target=collection_worker, daemon=True)
        self._collection_thread.start()

    def run(self, host: str = "127.0.0.1", port: int = 8050, debug: bool = False) -> None:
        """대시보드 서버 실행"""
        print(f"🚀 성능 대시보드 시작: http://{host}:{port}")
        print("Ctrl+C를 눌러 종료")

        # 데이터 수집 시작
        self.start_data_collection()

        try:
            self.app.run_server(host=host, port=port, debug=debug)
        except KeyboardInterrupt:
            print("\n대시보드 종료 중...")
        finally:
            self._stop_collection.set()
            if self._collection_thread and self._collection_thread.is_alive():
                self._collection_thread.join(timeout=2)


def main():
    """메인 실행 함수"""
    parser = argparse.ArgumentParser(description='GGC S3 Spooler 실시간 성능 대시보드')
    parser.add_argument('--host', type=str, default='127.0.0.1',
                       help='대시보드 서버 호스트 (기본: 127.0.0.1)')
    parser.add_argument('--port', type=int, default=8050,
                       help='대시보드 서버 포트 (기본: 8050)')
    parser.add_argument('--update-interval', type=float, default=2.0,
                       help='메트릭 업데이트 간격 (초, 기본: 2.0)')
    parser.add_argument('--debug', action='store_true',
                       help='디버그 모드 활성화')

    args = parser.parse_args()

    # 대시보드 생성 및 실행
    dashboard = PerformanceDashboard(
        update_interval_seconds=args.update_interval
    )

    dashboard.run(
        host=args.host,
        port=args.port,
        debug=args.debug
    )


if __name__ == "__main__":
    main()