"""pytest 공통 설정 및 픽스처."""

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "greengrass: 실제 Greengrass Core Device에서만 실행 가능한 테스트 "
        "(Stream Manager, recipe, IPC 등 Greengrass 런타임 필요)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """--greengrass 플래그 없으면 greengrass 마크 테스트를 자동 skip."""
    if config.getoption("--greengrass", default=False):
        return
    skip_marker = pytest.mark.skip(
        reason="Greengrass Core Device 환경 필요. --greengrass 플래그로 실행하세요."
    )
    for item in items:
        if item.get_closest_marker("greengrass"):
            item.add_marker(skip_marker)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--greengrass",
        action="store_true",
        default=False,
        help="Greengrass Core Device 전용 테스트를 포함하여 실행",
    )
