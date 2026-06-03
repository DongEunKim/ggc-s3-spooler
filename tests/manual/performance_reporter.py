#!/usr/bin/env python3
"""
GGC S3 Spooler 성능 분석 리포트 생성기.

테스트 결과 JSON 파일을 입력으로 받아 상세한 HTML 성능 분석 리포트를 생성한다.
차트, 표, 분석 결과를 포함한 종합 리포트를 제공한다.

사용법:
  python performance_reporter.py benchmark_results.json --output report.html
"""

import argparse
import base64
import io
import json
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

import matplotlib
matplotlib.use('Agg')  # 헤드리스 모드
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
import numpy as np

# 한글 폰트 설정
plt.rcParams['font.family'] = 'DejaVu Sans'
plt.rcParams['font.size'] = 10

# Seaborn 스타일 설정
sns.set_style("whitegrid")
sns.set_palette("husl")


class PerformanceReporter:
    """
    성능 테스트 결과를 분석하고 HTML 리포트를 생성하는 클래스.

    JSON 형태의 테스트 결과를 입력받아 차트, 표, 분석을 포함한
    종합적인 HTML 리포트를 생성한다.
    """

    def __init__(self, test_results: Dict[str, Any]):
        self.test_results = test_results
        self.charts: Dict[str, str] = {}  # base64 encoded charts

    def generate_report(self, output_path: Path) -> None:
        """종합 성능 리포트 생성"""
        print(f"📊 성능 리포트 생성 중...")

        # 차트 생성
        self._generate_all_charts()

        # HTML 리포트 생성
        html_content = self._generate_html_report()

        # 파일 저장
        output_path.write_text(html_content, encoding='utf-8')
        print(f"✅ 성능 리포트 생성 완료: {output_path}")

    def _generate_all_charts(self) -> None:
        """모든 차트 생성"""
        print("차트 생성 중...")

        # 1. 시나리오별 처리량 비교 차트
        self.charts['throughput_comparison'] = self._create_throughput_comparison_chart()

        # 2. 지연시간 분포 차트
        self.charts['latency_distribution'] = self._create_latency_distribution_chart()

        # 3. 처리량 포화점 분석 차트
        self.charts['throughput_saturation'] = self._create_throughput_saturation_chart()

        # 4. 멀티 프로세스 성능 분석
        self.charts['multiprocess_analysis'] = self._create_multiprocess_analysis_chart()

        # 5. 시스템 리소스 사용률
        self.charts['resource_utilization'] = self._create_resource_utilization_chart()

        # 6. 성능 요약 대시보드
        self.charts['performance_summary'] = self._create_performance_summary_chart()

        print("차트 생성 완료")

    def _create_throughput_comparison_chart(self) -> str:
        """시나리오별 처리량 비교 차트"""
        try:
            fig, ax = plt.subplots(figsize=(12, 6))

            scenarios = []
            throughputs = []
            colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728']

            for scenario_name, result in self.test_results.get('scenarios', {}).items():
                if 'error' in result:
                    continue

                results = result.get('results', {})
                throughput = results.get('throughput_files_per_second',
                                       results.get('concurrent_throughput', 0))
                if throughput > 0:
                    scenarios.append(self._format_scenario_name(scenario_name))
                    throughputs.append(throughput)

            if scenarios:
                bars = ax.bar(scenarios, throughputs, color=colors[:len(scenarios)], alpha=0.8)

                # 막대 위에 값 표시
                for bar, value in zip(bars, throughputs):
                    height = bar.get_height()
                    ax.text(bar.get_x() + bar.get_width()/2., height + height*0.01,
                           f'{value:.1f}',
                           ha='center', va='bottom', fontweight='bold')

                ax.set_title('시나리오별 처리량 비교', fontsize=16, fontweight='bold', pad=20)
                ax.set_ylabel('처리량 (파일/초)', fontsize=12)
                ax.set_xlabel('테스트 시나리오', fontsize=12)

                # Y축 범위 조정
                max_throughput = max(throughputs) if throughputs else 1
                ax.set_ylim(0, max_throughput * 1.2)

                # 그리드 스타일
                ax.grid(True, alpha=0.3)

                plt.xticks(rotation=45, ha='right')
                plt.tight_layout()

            return self._encode_chart_to_base64(fig)

        except Exception as e:
            print(f"처리량 비교 차트 생성 오류: {e}")
            return self._create_error_chart("처리량 비교", str(e))

    def _create_latency_distribution_chart(self) -> str:
        """지연시간 분포 차트"""
        try:
            latency_result = self.test_results.get('scenarios', {}).get('latency_distribution')
            if not latency_result or 'error' in latency_result:
                return self._create_empty_chart("지연시간 분포", "지연시간 데이터 없음")

            latency_stats = latency_result.get('results', {}).get('latency_statistics', {})
            if not latency_stats or 'error' in latency_stats:
                return self._create_empty_chart("지연시간 분포", "지연시간 통계 없음")

            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))

            # 백분위 차트
            percentiles = ['P50', 'P75', 'P90', 'P95', 'P99']
            values = [
                latency_stats.get('p50', 0),
                latency_stats.get('p75', 0),
                latency_stats.get('p90', 0),
                latency_stats.get('p95', 0),
                latency_stats.get('p99', 0)
            ]

            colors = sns.color_palette("viridis", len(percentiles))
            bars = ax1.bar(percentiles, values, color=colors, alpha=0.8)

            for bar, value in zip(bars, values):
                height = bar.get_height()
                ax1.text(bar.get_x() + bar.get_width()/2., height + height*0.01,
                        f'{value:.3f}s',
                        ha='center', va='bottom', fontweight='bold')

            ax1.set_title('지연시간 백분위 분포', fontsize=14, fontweight='bold')
            ax1.set_ylabel('지연시간 (초)')
            ax1.grid(True, alpha=0.3)

            # 통계 요약 차트
            stats_labels = ['평균', '중앙값', '표준편차', '최소', '최대']
            stats_values = [
                latency_stats.get('mean', 0),
                latency_stats.get('median', 0),
                latency_stats.get('std_dev', 0),
                latency_stats.get('min', 0),
                latency_stats.get('max', 0)
            ]

            colors2 = sns.color_palette("rocket", len(stats_labels))
            bars2 = ax2.barh(stats_labels, stats_values, color=colors2, alpha=0.8)

            for bar, value in zip(bars2, stats_values):
                width = bar.get_width()
                ax2.text(width + width*0.01, bar.get_y() + bar.get_height()/2.,
                        f'{value:.3f}s',
                        ha='left', va='center', fontweight='bold')

            ax2.set_title('지연시간 통계 요약', fontsize=14, fontweight='bold')
            ax2.set_xlabel('지연시간 (초)')
            ax2.grid(True, alpha=0.3)

            plt.suptitle(f'지연시간 분석 ({latency_stats.get("count", 0)}개 샘플)',
                        fontsize=16, fontweight='bold')
            plt.tight_layout()

            return self._encode_chart_to_base64(fig)

        except Exception as e:
            print(f"지연시간 분포 차트 생성 오류: {e}")
            return self._create_error_chart("지연시간 분포", str(e))

    def _create_throughput_saturation_chart(self) -> str:
        """처리량 포화점 분석 차트"""
        try:
            saturation_result = self.test_results.get('scenarios', {}).get('throughput_saturation')
            if not saturation_result or 'error' in saturation_result:
                return self._create_empty_chart("처리량 포화점", "포화점 테스트 데이터 없음")

            phase_results = saturation_result.get('results', {}).get('phase_results', [])
            if not phase_results:
                return self._create_empty_chart("처리량 포화점", "포화점 단계 데이터 없음")

            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))

            # 데이터 준비
            phases = [f"단계 {p['phase']}" for p in phase_results]
            target_rates = [p['target_rate'] for p in phase_results]
            actual_rates = [p['processing_rate'] for p in phase_results]
            saturation_ratios = [p['saturation_ratio'] for p in phase_results]

            x = np.arange(len(phases))
            width = 0.35

            # 목표 vs 실제 처리율
            bars1 = ax1.bar(x - width/2, target_rates, width, label='목표 처리율', alpha=0.8, color='#1f77b4')
            bars2 = ax1.bar(x + width/2, actual_rates, width, label='실제 처리율', alpha=0.8, color='#ff7f0e')

            ax1.set_title('처리량 포화점 분석', fontsize=14, fontweight='bold')
            ax1.set_xlabel('부하 단계')
            ax1.set_ylabel('처리율 (파일/초)')
            ax1.set_xticks(x)
            ax1.set_xticklabels(phases)
            ax1.legend()
            ax1.grid(True, alpha=0.3)

            # 포화율
            colors = ['green' if ratio >= 0.9 else 'orange' if ratio >= 0.7 else 'red' for ratio in saturation_ratios]
            bars3 = ax2.bar(phases, [r * 100 for r in saturation_ratios], color=colors, alpha=0.8)

            for bar, ratio in zip(bars3, saturation_ratios):
                height = bar.get_height()
                ax2.text(bar.get_x() + bar.get_width()/2., height + 1,
                        f'{ratio:.1%}',
                        ha='center', va='bottom', fontweight='bold')

            ax2.set_title('포화율 (실제/목표)', fontsize=14, fontweight='bold')
            ax2.set_xlabel('부하 단계')
            ax2.set_ylabel('포화율 (%)')
            ax2.axhline(y=90, color='red', linestyle='--', alpha=0.7, label='포화점 (90%)')
            ax2.legend()
            ax2.grid(True, alpha=0.3)

            plt.xticks(rotation=45)
            plt.tight_layout()

            return self._encode_chart_to_base64(fig)

        except Exception as e:
            print(f"포화점 분석 차트 생성 오류: {e}")
            return self._create_error_chart("처리량 포화점", str(e))

    def _create_multiprocess_analysis_chart(self) -> str:
        """멀티 프로세스 성능 분석 차트"""
        try:
            mp_result = self.test_results.get('scenarios', {}).get('multi_process_concurrency')
            if not mp_result or 'error' in mp_result:
                return self._create_empty_chart("멀티 프로세스 분석", "멀티 프로세스 데이터 없음")

            results = mp_result.get('results', {})
            worker_results = results.get('worker_results', [])

            if not worker_results:
                return self._create_empty_chart("멀티 프로세스 분석", "작업자 결과 없음")

            fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(15, 12))

            # 워커별 생성 파일 수
            worker_ids = [f"프로세스 {i}" for i in range(len(worker_results))]
            files_created = [w['files_created'] for w in worker_results]
            errors = [w['errors'] for w in worker_results]
            creation_times = [w['creation_time'] for w in worker_results]

            ax1.bar(worker_ids, files_created, color='#2ca02c', alpha=0.8)
            ax1.set_title('워커별 생성 파일 수', fontweight='bold')
            ax1.set_ylabel('파일 수')
            ax1.grid(True, alpha=0.3)

            # 워커별 오류 수
            ax2.bar(worker_ids, errors, color='#d62728', alpha=0.8)
            ax2.set_title('워커별 오류 수', fontweight='bold')
            ax2.set_ylabel('오류 수')
            ax2.grid(True, alpha=0.3)

            # 워커별 생성 시간
            ax3.bar(worker_ids, creation_times, color='#ff7f0e', alpha=0.8)
            ax3.set_title('워커별 생성 소요 시간', fontweight='bold')
            ax3.set_ylabel('시간 (초)')
            ax3.grid(True, alpha=0.3)

            # 처리량 분석
            total_files = results.get('files_created', 0)
            total_processed = results.get('files_processed', 0)
            processing_time = results.get('total_processing_time', 1)

            metrics = ['생성률\n(파일/초)', '처리률\n(파일/초)', '성공률\n(%)', '동시성\n효율']
            values = [
                total_files / processing_time,
                total_processed / processing_time,
                (total_processed / total_files * 100) if total_files > 0 else 0,
                results.get('concurrent_throughput', 0)
            ]

            colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#9467bd']
            ax4.bar(metrics, values, color=colors, alpha=0.8)
            ax4.set_title('종합 성능 지표', fontweight='bold')
            ax4.grid(True, alpha=0.3)

            for bar, value in zip(ax4.patches, values):
                height = bar.get_height()
                ax4.text(bar.get_x() + bar.get_width()/2., height + height*0.01,
                        f'{value:.1f}',
                        ha='center', va='bottom', fontweight='bold', fontsize=10)

            plt.suptitle('멀티 프로세스 동시성 분석', fontsize=16, fontweight='bold')
            plt.tight_layout()

            return self._encode_chart_to_base64(fig)

        except Exception as e:
            print(f"멀티 프로세스 차트 생성 오류: {e}")
            return self._create_error_chart("멀티 프로세스 분석", str(e))

    def _create_resource_utilization_chart(self) -> str:
        """시스템 리소스 사용률 차트"""
        try:
            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))

            # 시나리오별 최대 메모리 사용량 수집
            scenarios = []
            max_memories = []
            avg_cpus = []

            for scenario_name, result in self.test_results.get('scenarios', {}).items():
                if 'error' in result:
                    continue

                metrics = result.get('metrics', {})
                system_metrics = metrics.get('system_metrics', {})

                if system_metrics:
                    scenarios.append(self._format_scenario_name(scenario_name))
                    max_memories.append(system_metrics.get('memory_rss_mb', 0))
                    avg_cpus.append(system_metrics.get('cpu_percent', 0))

            if scenarios:
                # 메모리 사용률
                bars1 = ax1.bar(scenarios, max_memories, color='#2ca02c', alpha=0.8)
                ax1.set_title('시나리오별 최대 메모리 사용량', fontweight='bold')
                ax1.set_ylabel('메모리 (MB)')
                ax1.grid(True, alpha=0.3)

                for bar, value in zip(bars1, max_memories):
                    height = bar.get_height()
                    ax1.text(bar.get_x() + bar.get_width()/2., height + height*0.01,
                            f'{value:.1f}MB',
                            ha='center', va='bottom', fontsize=10)

                # CPU 사용률
                bars2 = ax2.bar(scenarios, avg_cpus, color='#d62728', alpha=0.8)
                ax2.set_title('시나리오별 평균 CPU 사용률', fontweight='bold')
                ax2.set_ylabel('CPU (%)')
                ax2.grid(True, alpha=0.3)

                for bar, value in zip(bars2, avg_cpus):
                    height = bar.get_height()
                    ax2.text(bar.get_x() + bar.get_width()/2., height + height*0.01,
                            f'{value:.1f}%',
                            ha='center', va='bottom', fontsize=10)

                plt.xticks(rotation=45, ha='right')
                plt.tight_layout()
            else:
                # 데이터가 없는 경우
                ax1.text(0.5, 0.5, '리소스 데이터 없음', ha='center', va='center', transform=ax1.transAxes)
                ax2.text(0.5, 0.5, '리소스 데이터 없음', ha='center', va='center', transform=ax2.transAxes)

            return self._encode_chart_to_base64(fig)

        except Exception as e:
            print(f"리소스 사용률 차트 생성 오류: {e}")
            return self._create_error_chart("리소스 사용률", str(e))

    def _create_performance_summary_chart(self) -> str:
        """성능 요약 대시보드"""
        try:
            fig = plt.figure(figsize=(16, 10))
            gs = fig.add_gridspec(3, 3, hspace=0.3, wspace=0.3)

            summary = self.test_results.get('summary', {})

            # 1. 전체 성능 지표 (상단 왼쪽)
            ax1 = fig.add_subplot(gs[0, 0])
            metrics = ['총 파일', '최대 처리량', '성공 시나리오']
            values = [
                summary.get('total_files_processed', 0),
                summary.get('peak_throughput', 0),
                summary.get('success_scenarios', 0)
            ]

            ax1.bar(metrics, values, color=['#1f77b4', '#ff7f0e', '#2ca02c'], alpha=0.8)
            ax1.set_title('전체 성능 지표', fontweight='bold')
            for i, v in enumerate(values):
                ax1.text(i, v + max(values)*0.01, f'{v:.1f}' if isinstance(v, float) else str(v),
                        ha='center', va='bottom', fontweight='bold')

            # 2. 시나리오 성공률 (상단 중앙)
            ax2 = fig.add_subplot(gs[0, 1])
            success_count = summary.get('success_scenarios', 0)
            total_count = summary.get('scenario_count', 1)
            success_rate = success_count / total_count * 100 if total_count > 0 else 0

            colors = ['#2ca02c' if success_rate >= 80 else '#ff7f0e' if success_rate >= 60 else '#d62728']
            ax2.pie([success_rate, 100-success_rate], labels=['성공', '실패'],
                   colors=['#2ca02c', '#ffcccc'], autopct='%1.1f%%', startangle=90)
            ax2.set_title(f'시나리오 성공률\n({success_count}/{total_count})', fontweight='bold')

            # 3. 테스트 환경 정보 (상단 오른쪽)
            ax3 = fig.add_subplot(gs[0, 2])
            ax3.axis('off')
            test_info = self.test_results.get('test_info', {})

            info_text = []
            if test_info.get('use_realistic_mock'):
                info_text.append("✅ 현실적 Mock 사용")
                sim_params = test_info.get('simulation_params', {})
                if sim_params:
                    info_text.append(f"네트워크 지연: {sim_params.get('network_latency_range', 'N/A')}")
                    info_text.append(f"대역폭: {sim_params.get('bandwidth_limit_mbps', 'N/A')}MB/s")
                    info_text.append(f"오류율: {sim_params.get('error_rate', 0)*100:.1f}%")
            else:
                info_text.append("🔹 기본 Mock 사용")

            test_time = datetime.fromtimestamp(test_info.get('timestamp', 0))
            info_text.append(f"테스트 시간: {test_time.strftime('%Y-%m-%d %H:%M')}")

            ax3.text(0.1, 0.8, '테스트 환경', fontsize=14, fontweight='bold')
            for i, text in enumerate(info_text):
                ax3.text(0.1, 0.6 - i*0.1, text, fontsize=10)

            # 4. 처리량 트렌드 (중단)
            ax4 = fig.add_subplot(gs[1, :])
            scenario_throughputs = []
            scenario_names = []

            for scenario_name, result in self.test_results.get('scenarios', {}).items():
                if 'error' in result:
                    continue
                results = result.get('results', {})
                throughput = results.get('throughput_files_per_second',
                                       results.get('concurrent_throughput', 0))
                if throughput > 0:
                    scenario_names.append(self._format_scenario_name(scenario_name))
                    scenario_throughputs.append(throughput)

            if scenario_names:
                ax4.plot(scenario_names, scenario_throughputs, 'o-', linewidth=2, markersize=8)
                ax4.set_title('시나리오별 처리량 트렌드', fontsize=14, fontweight='bold')
                ax4.set_ylabel('처리량 (파일/초)')
                ax4.grid(True, alpha=0.3)
                plt.setp(ax4.xaxis.get_majorticklabels(), rotation=45, ha='right')

            # 5. 상세 분석 테이블 (하단)
            ax5 = fig.add_subplot(gs[2, :])
            ax5.axis('off')

            # 테이블 데이터 준비
            table_data = []
            for scenario_name, result in self.test_results.get('scenarios', {}).items():
                if 'error' in result:
                    table_data.append([
                        self._format_scenario_name(scenario_name),
                        '실패',
                        result.get('error', '')[:50] + '...' if len(result.get('error', '')) > 50 else result.get('error', ''),
                        '',
                        ''
                    ])
                else:
                    results = result.get('results', {})
                    table_data.append([
                        self._format_scenario_name(scenario_name),
                        '성공',
                        f"{results.get('files_processed', 0)}",
                        f"{results.get('throughput_files_per_second', results.get('concurrent_throughput', 0)):.1f}",
                        f"{results.get('success_rate', 100):.1f}%"
                    ])

            if table_data:
                table = ax5.table(
                    cellText=table_data,
                    colLabels=['시나리오', '상태', '처리파일/오류', '처리량', '성공률'],
                    cellLoc='center',
                    loc='center',
                    bbox=[0, 0, 1, 1]
                )
                table.auto_set_font_size(False)
                table.set_fontsize(9)
                table.scale(1, 2)

                # 헤더 스타일
                for i in range(5):
                    table[(0, i)].set_facecolor('#40466e')
                    table[(0, i)].set_text_props(weight='bold', color='white')

                # 성공/실패 색상
                for i, row in enumerate(table_data, 1):
                    if row[1] == '성공':
                        table[(i, 1)].set_facecolor('#d4edda')
                    else:
                        table[(i, 1)].set_facecolor('#f8d7da')

            plt.suptitle('GGC S3 Spooler 성능 테스트 종합 요약', fontsize=18, fontweight='bold', y=0.98)

            return self._encode_chart_to_base64(fig)

        except Exception as e:
            print(f"성능 요약 차트 생성 오류: {e}")
            return self._create_error_chart("성능 요약", str(e))

    def _format_scenario_name(self, scenario_name: str) -> str:
        """시나리오 이름을 보기 좋게 포맷"""
        name_map = {
            'concurrent_small_files': '소형 파일\n동시 처리',
            'multi_process_concurrency': '멀티 프로세스\n동시성',
            'throughput_saturation': '처리량\n포화점',
            'latency_distribution': '지연시간\n분포'
        }
        return name_map.get(scenario_name, scenario_name.replace('_', ' ').title())

    def _encode_chart_to_base64(self, fig) -> str:
        """차트를 base64 문자열로 인코딩"""
        buffer = io.BytesIO()
        fig.savefig(buffer, format='png', dpi=150, bbox_inches='tight',
                   facecolor='white', edgecolor='none')
        buffer.seek(0)
        chart_data = base64.b64encode(buffer.read()).decode()
        plt.close(fig)
        return chart_data

    def _create_empty_chart(self, title: str, message: str) -> str:
        """빈 차트 생성"""
        fig, ax = plt.subplots(figsize=(8, 6))
        ax.text(0.5, 0.5, message, ha='center', va='center',
               transform=ax.transAxes, fontsize=14, style='italic')
        ax.set_title(title, fontsize=16, fontweight='bold')
        ax.axis('off')
        return self._encode_chart_to_base64(fig)

    def _create_error_chart(self, title: str, error_msg: str) -> str:
        """오류 차트 생성"""
        fig, ax = plt.subplots(figsize=(8, 6))
        ax.text(0.5, 0.5, f"오류: {error_msg}", ha='center', va='center',
               transform=ax.transAxes, fontsize=12, color='red')
        ax.set_title(title, fontsize=16, fontweight='bold')
        ax.axis('off')
        return self._encode_chart_to_base64(fig)

    def _generate_html_report(self) -> str:
        """HTML 리포트 생성"""
        html_template = """
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GGC S3 Spooler 성능 분석 리포트</title>
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f8f9fa;
            color: #333;
            line-height: 1.6;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            padding: 30px;
            border-radius: 10px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }
        .header {
            text-align: center;
            margin-bottom: 40px;
            padding-bottom: 20px;
            border-bottom: 3px solid #007bff;
        }
        .header h1 {
            color: #007bff;
            margin: 0;
            font-size: 2.5em;
        }
        .header p {
            color: #666;
            margin: 10px 0 0 0;
            font-size: 1.1em;
        }
        .summary-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 40px;
        }
        .summary-card {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 25px;
            border-radius: 10px;
            text-align: center;
        }
        .summary-card h3 {
            margin: 0 0 10px 0;
            font-size: 1.1em;
            opacity: 0.9;
        }
        .summary-card .value {
            font-size: 2.5em;
            font-weight: bold;
            margin: 10px 0;
        }
        .summary-card .unit {
            font-size: 0.9em;
            opacity: 0.8;
        }
        .section {
            margin-bottom: 50px;
        }
        .section h2 {
            color: #333;
            border-bottom: 2px solid #e9ecef;
            padding-bottom: 10px;
            margin-bottom: 30px;
            font-size: 1.8em;
        }
        .chart-container {
            text-align: center;
            margin-bottom: 40px;
            background: #fafafa;
            padding: 20px;
            border-radius: 8px;
            border: 1px solid #e9ecef;
        }
        .chart-container img {
            max-width: 100%;
            height: auto;
            border-radius: 5px;
        }
        .analysis-text {
            background: #f8f9fa;
            padding: 20px;
            border-radius: 8px;
            border-left: 4px solid #007bff;
            margin-bottom: 30px;
        }
        .analysis-text h3 {
            margin-top: 0;
            color: #007bff;
        }
        .recommendations {
            background: #d1ecf1;
            padding: 20px;
            border-radius: 8px;
            border: 1px solid #bee5eb;
        }
        .recommendations h3 {
            margin-top: 0;
            color: #0c5460;
        }
        .recommendations ul {
            margin-bottom: 0;
        }
        .table-container {
            overflow-x: auto;
            margin-bottom: 30px;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            background: white;
        }
        table th {
            background: #007bff;
            color: white;
            padding: 12px;
            text-align: left;
            font-weight: bold;
        }
        table td {
            padding: 12px;
            border-bottom: 1px solid #e9ecef;
        }
        table tr:nth-child(even) {
            background: #f8f9fa;
        }
        .status-success {
            background: #d4edda !important;
            color: #155724;
            font-weight: bold;
        }
        .status-error {
            background: #f8d7da !important;
            color: #721c24;
            font-weight: bold;
        }
        .footer {
            text-align: center;
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid #e9ecef;
            color: #666;
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- 헤더 -->
        <div class="header">
            <h1>📊 GGC S3 Spooler 성능 분석 리포트</h1>
            <p>종합 고부하 테스트 결과 분석 • {timestamp}</p>
        </div>

        <!-- 핵심 지표 요약 -->
        <div class="summary-grid">
            <div class="summary-card">
                <h3>총 처리 파일</h3>
                <div class="value">{total_files:,}</div>
                <div class="unit">파일</div>
            </div>
            <div class="summary-card">
                <h3>최대 처리량</h3>
                <div class="value">{peak_throughput:.1f}</div>
                <div class="unit">파일/초</div>
            </div>
            <div class="summary-card">
                <h3>평균 지연시간</h3>
                <div class="value">{avg_latency:.3f}</div>
                <div class="unit">초</div>
            </div>
            <div class="summary-card">
                <h3>성공률</h3>
                <div class="value">{success_rate:.1f}</div>
                <div class="unit">%</div>
            </div>
        </div>

        <!-- 성능 요약 대시보드 -->
        <div class="section">
            <h2>🎯 성능 요약 대시보드</h2>
            <div class="chart-container">
                <img src="data:image/png;base64,{performance_summary}" alt="성능 요약 대시보드">
            </div>
        </div>

        <!-- 처리량 분석 -->
        <div class="section">
            <h2>⚡ 처리량 분석</h2>
            <div class="chart-container">
                <img src="data:image/png;base64,{throughput_comparison}" alt="시나리오별 처리량 비교">
            </div>

            <div class="chart-container">
                <img src="data:image/png;base64,{throughput_saturation}" alt="처리량 포화점 분석">
            </div>

            <div class="analysis-text">
                <h3>📈 처리량 분석 결과</h3>
                <p><strong>최대 처리량:</strong> {peak_throughput:.1f} 파일/초</p>
                <p><strong>포화점:</strong> {saturation_point}</p>
                <p><strong>분석:</strong> {throughput_analysis}</p>
            </div>
        </div>

        <!-- 지연시간 분석 -->
        <div class="section">
            <h2>⏱️ 지연시간 분석</h2>
            <div class="chart-container">
                <img src="data:image/png;base64,{latency_distribution}" alt="지연시간 분포 분석">
            </div>

            <div class="analysis-text">
                <h3>📊 지연시간 분석 결과</h3>
                <p><strong>평균 지연시간:</strong> {avg_latency:.3f}초</p>
                <p><strong>P90 지연시간:</strong> {p90_latency:.3f}초</p>
                <p><strong>P99 지연시간:</strong> {p99_latency:.3f}초</p>
                <p><strong>분석:</strong> {latency_analysis}</p>
            </div>
        </div>

        <!-- 동시성 분석 -->
        <div class="section">
            <h2>🔄 동시성 분석</h2>
            <div class="chart-container">
                <img src="data:image/png;base64,{multiprocess_analysis}" alt="멀티 프로세스 분석">
            </div>

            <div class="analysis-text">
                <h3>🚀 동시성 성능 결과</h3>
                <p><strong>동시 프로세스 수:</strong> {concurrent_processes}</p>
                <p><strong>동시성 처리량:</strong> {concurrent_throughput:.1f} 파일/초</p>
                <p><strong>분석:</strong> {concurrency_analysis}</p>
            </div>
        </div>

        <!-- 리소스 사용률 -->
        <div class="section">
            <h2>💻 리소스 사용률</h2>
            <div class="chart-container">
                <img src="data:image/png;base64,{resource_utilization}" alt="시스템 리소스 사용률">
            </div>

            <div class="analysis-text">
                <h3>🖥️ 리소스 분석 결과</h3>
                <p><strong>최대 메모리 사용량:</strong> {max_memory:.1f}MB</p>
                <p><strong>평균 CPU 사용률:</strong> {avg_cpu:.1f}%</p>
                <p><strong>분석:</strong> {resource_analysis}</p>
            </div>
        </div>

        <!-- 시나리오별 상세 결과 -->
        <div class="section">
            <h2>📋 시나리오별 상세 결과</h2>
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>시나리오</th>
                            <th>상태</th>
                            <th>처리 파일</th>
                            <th>처리량 (파일/초)</th>
                            <th>성공률 (%)</th>
                            <th>소요 시간 (초)</th>
                            <th>비고</th>
                        </tr>
                    </thead>
                    <tbody>
                        {scenario_table_rows}
                    </tbody>
                </table>
            </div>
        </div>

        <!-- 성능 권고사항 -->
        <div class="section">
            <div class="recommendations">
                <h3>🎯 성능 최적화 권고사항</h3>
                <ul>
                    {recommendations}
                </ul>
            </div>
        </div>

        <!-- 테스트 환경 정보 -->
        <div class="section">
            <h2>🔧 테스트 환경 정보</h2>
            <div class="table-container">
                <table>
                    <tbody>
                        <tr>
                            <td><strong>Mock 유형</strong></td>
                            <td>{mock_type}</td>
                        </tr>
                        <tr>
                            <td><strong>네트워크 시뮬레이션</strong></td>
                            <td>{network_simulation}</td>
                        </tr>
                        <tr>
                            <td><strong>테스트 시간</strong></td>
                            <td>{test_timestamp}</td>
                        </tr>
                        <tr>
                            <td><strong>총 테스트 시나리오</strong></td>
                            <td>{total_scenarios}개</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>

        <div class="footer">
            <p>🤖 GGC S3 Spooler 자동 성능 리포트 • 생성 시간: {report_time}</p>
        </div>
    </div>
</body>
</html>
        """

        # 데이터 준비
        summary = self.test_results.get('summary', {})
        test_info = self.test_results.get('test_info', {})

        # 지연시간 데이터 추출
        latency_result = self.test_results.get('scenarios', {}).get('latency_distribution', {})
        latency_stats = latency_result.get('results', {}).get('latency_statistics', {})

        # 시나리오 테이블 행 생성
        scenario_rows = []
        for scenario_name, result in self.test_results.get('scenarios', {}).items():
            if 'error' in result:
                scenario_rows.append(f"""
                    <tr>
                        <td>{self._format_scenario_name(scenario_name)}</td>
                        <td class="status-error">실패</td>
                        <td>-</td>
                        <td>-</td>
                        <td>-</td>
                        <td>-</td>
                        <td>{result.get('error', '')[:100]}...</td>
                    </tr>
                """)
            else:
                results = result.get('results', {})
                scenario_rows.append(f"""
                    <tr>
                        <td>{self._format_scenario_name(scenario_name)}</td>
                        <td class="status-success">성공</td>
                        <td>{results.get('files_processed', 0):,}</td>
                        <td>{results.get('throughput_files_per_second', results.get('concurrent_throughput', 0)):.1f}</td>
                        <td>{results.get('success_rate', 100):.1f}</td>
                        <td>{results.get('total_processing_time', 0):.1f}</td>
                        <td>정상 완료</td>
                    </tr>
                """)

        # 권고사항 생성
        recommendations = self._generate_recommendations()

        # HTML 템플릿 채우기
        return html_template.format(
            timestamp=datetime.now().strftime("%Y년 %m월 %d일 %H:%M"),
            total_files=summary.get('total_files_processed', 0),
            peak_throughput=summary.get('peak_throughput', 0),
            avg_latency=summary.get('average_latency_p50', 0),
            success_rate=summary.get('success_scenarios', 0) / summary.get('scenario_count', 1) * 100,

            # 차트 이미지들
            performance_summary=self.charts.get('performance_summary', ''),
            throughput_comparison=self.charts.get('throughput_comparison', ''),
            throughput_saturation=self.charts.get('throughput_saturation', ''),
            latency_distribution=self.charts.get('latency_distribution', ''),
            multiprocess_analysis=self.charts.get('multiprocess_analysis', ''),
            resource_utilization=self.charts.get('resource_utilization', ''),

            # 분석 내용
            saturation_point=f"{summary.get('saturation_point', 'N/A')} 파일/초" if summary.get('saturation_point') else "측정 범위 내 미달",
            throughput_analysis=self._analyze_throughput(),
            p90_latency=latency_stats.get('p90', 0),
            p99_latency=latency_stats.get('p99', 0),
            latency_analysis=self._analyze_latency(),
            concurrent_processes=self._get_concurrent_process_count(),
            concurrent_throughput=self._get_concurrent_throughput(),
            concurrency_analysis=self._analyze_concurrency(),
            max_memory=self._get_max_memory(),
            avg_cpu=self._get_avg_cpu(),
            resource_analysis=self._analyze_resources(),

            # 테이블 및 기타
            scenario_table_rows=''.join(scenario_rows),
            recommendations=''.join(f'<li>{rec}</li>' for rec in recommendations),

            # 환경 정보
            mock_type="현실적 Mock (네트워크 시뮬레이션)" if test_info.get('use_realistic_mock') else "기본 Mock",
            network_simulation=str(test_info.get('simulation_params', {})) if test_info.get('use_realistic_mock') else "없음",
            test_timestamp=datetime.fromtimestamp(test_info.get('timestamp', 0)).strftime("%Y-%m-%d %H:%M:%S"),
            total_scenarios=summary.get('scenario_count', 0),
            report_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )

    def _analyze_throughput(self) -> str:
        """처리량 분석 텍스트 생성"""
        peak = self.test_results.get('summary', {}).get('peak_throughput', 0)
        if peak > 100:
            return "매우 높은 처리량을 보여주며, 대용량 파일 처리에 적합합니다."
        elif peak > 50:
            return "양호한 처리량을 보여주며, 중간 규모 워크로드에 적합합니다."
        elif peak > 20:
            return "보통 수준의 처리량이며, 소규모 워크로드에 적합합니다."
        else:
            return "처리량이 낮아 성능 최적화가 필요합니다."

    def _analyze_latency(self) -> str:
        """지연시간 분석 텍스트 생성"""
        latency_result = self.test_results.get('scenarios', {}).get('latency_distribution', {})
        latency_stats = latency_result.get('results', {}).get('latency_statistics', {})

        p90 = latency_stats.get('p90', 0)
        if p90 < 0.1:
            return "매우 낮은 지연시간으로 실시간 처리에 적합합니다."
        elif p90 < 0.5:
            return "양호한 지연시간 수준입니다."
        elif p90 < 1.0:
            return "보통 수준의 지연시간이지만 개선 여지가 있습니다."
        else:
            return "높은 지연시간으로 최적화가 필요합니다."

    def _analyze_concurrency(self) -> str:
        """동시성 분석 텍스트 생성"""
        mp_result = self.test_results.get('scenarios', {}).get('multi_process_concurrency', {})
        if 'error' in mp_result:
            return "동시성 테스트가 실패하여 분석할 수 없습니다."

        results = mp_result.get('results', {})
        success_rate = results.get('success_rate', 0)

        if success_rate > 95:
            return "우수한 동시성 처리 능력을 보여줍니다."
        elif success_rate > 85:
            return "양호한 동시성 처리 성능입니다."
        else:
            return "동시성 처리에서 문제가 발견되어 개선이 필요합니다."

    def _analyze_resources(self) -> str:
        """리소스 분석 텍스트 생성"""
        max_memory = self._get_max_memory()
        avg_cpu = self._get_avg_cpu()

        analysis_parts = []

        if max_memory > 1000:
            analysis_parts.append("메모리 사용량이 높아 최적화가 필요합니다")
        elif max_memory > 500:
            analysis_parts.append("메모리 사용량이 보통 수준입니다")
        else:
            analysis_parts.append("메모리 사용량이 효율적입니다")

        if avg_cpu > 80:
            analysis_parts.append("CPU 사용률이 높습니다")
        elif avg_cpu > 50:
            analysis_parts.append("CPU 사용률이 보통 수준입니다")
        else:
            analysis_parts.append("CPU 사용률이 효율적입니다")

        return ". ".join(analysis_parts) + "."

    def _get_concurrent_process_count(self) -> int:
        """동시 프로세스 수 조회"""
        mp_result = self.test_results.get('scenarios', {}).get('multi_process_concurrency', {})
        return mp_result.get('parameters', {}).get('process_count', 0)

    def _get_concurrent_throughput(self) -> float:
        """동시성 처리량 조회"""
        mp_result = self.test_results.get('scenarios', {}).get('multi_process_concurrency', {})
        return mp_result.get('results', {}).get('concurrent_throughput', 0)

    def _get_max_memory(self) -> float:
        """최대 메모리 사용량 조회"""
        max_memory = 0
        for result in self.test_results.get('scenarios', {}).values():
            if 'error' in result:
                continue
            memory = result.get('metrics', {}).get('system_metrics', {}).get('memory_rss_mb', 0)
            max_memory = max(max_memory, memory)
        return max_memory

    def _get_avg_cpu(self) -> float:
        """평균 CPU 사용률 조회"""
        cpu_values = []
        for result in self.test_results.get('scenarios', {}).values():
            if 'error' in result:
                continue
            cpu = result.get('metrics', {}).get('system_metrics', {}).get('cpu_percent', 0)
            if cpu > 0:
                cpu_values.append(cpu)
        return statistics.mean(cpu_values) if cpu_values else 0

    def _generate_recommendations(self) -> List[str]:
        """성능 최적화 권고사항 생성"""
        recommendations = []

        # 처리량 기반 권고
        peak_throughput = self.test_results.get('summary', {}).get('peak_throughput', 0)
        if peak_throughput < 50:
            recommendations.append("처리량이 낮습니다. 비동기 처리 최적화와 큐 관리 개선을 검토하세요.")

        # 지연시간 기반 권고
        latency_result = self.test_results.get('scenarios', {}).get('latency_distribution', {})
        latency_stats = latency_result.get('results', {}).get('latency_statistics', {})
        p90 = latency_stats.get('p90', 0)
        if p90 > 0.5:
            recommendations.append("P90 지연시간이 높습니다. 파일 안정성 검증 로직과 Stream Manager 연결 최적화를 검토하세요.")

        # 메모리 기반 권고
        max_memory = self._get_max_memory()
        if max_memory > 512:
            recommendations.append("메모리 사용량이 높습니다. 메트릭 히스토리 제한과 파일 버퍼링 최적화를 검토하세요.")

        # 동시성 기반 권고
        mp_result = self.test_results.get('scenarios', {}).get('multi_process_concurrency', {})
        if mp_result and 'error' not in mp_result:
            success_rate = mp_result.get('results', {}).get('success_rate', 100)
            if success_rate < 90:
                recommendations.append("멀티 프로세스 동시성에서 문제가 발견되었습니다. 파일 잠금과 경쟁 조건을 검토하세요.")

        # 포화점 기반 권고
        saturation_point = self.test_results.get('summary', {}).get('saturation_point')
        if saturation_point and saturation_point < 100:
            recommendations.append(f"처리량 포화점이 {saturation_point}파일/초입니다. executor 스레드풀 크기 조정을 검토하세요.")

        # 기본 권고사항
        if not recommendations:
            recommendations.extend([
                "전반적으로 양호한 성능을 보여줍니다.",
                "정기적인 성능 모니터링을 통해 성능 회귀를 감지하세요.",
                "TGU 실제 환경에서의 검증을 수행하세요."
            ])

        return recommendations


def main():
    """메인 실행 함수"""
    parser = argparse.ArgumentParser(description='GGC S3 Spooler 성능 리포트 생성기')
    parser.add_argument('input_file', type=str,
                       help='테스트 결과 JSON 파일')
    parser.add_argument('--output', type=str, default='performance_report.html',
                       help='출력 HTML 파일명 (기본: performance_report.html)')

    args = parser.parse_args()

    input_path = Path(args.input_file)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"❌ 입력 파일을 찾을 수 없습니다: {input_path}")
        return 1

    try:
        # 테스트 결과 로드
        test_results = json.loads(input_path.read_text())

        # 리포터 생성 및 실행
        reporter = PerformanceReporter(test_results)
        reporter.generate_report(output_path)

        print(f"🎉 성능 리포트가 성공적으로 생성되었습니다!")
        print(f"📂 파일 위치: {output_path.absolute()}")
        print(f"🌐 브라우저에서 열기: file://{output_path.absolute()}")

        return 0

    except Exception as e:
        print(f"❌ 리포트 생성 중 오류 발생: {e}")
        return 1


if __name__ == "__main__":
    exit(main())