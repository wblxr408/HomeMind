# HomeMind Makefile

.PHONY: help install test test-unit test-integration test-e2e test-benchmark test-all lint clean run

help:
	@echo "HomeMind 开发命令"
	@echo "=================="
	@echo "  make install          安装依赖 (pip install -r requirements-web.txt)"
	@echo "  make test             运行全部测试"
	@echo "  make test-unit        运行单元测试"
	@echo "  make test-integration 运行集成测试"
	@echo "  make test-e2e        运行 E2E 场景测试"
	@echo "  make test-benchmark  运行性能基准测试"
	@echo "  make test-all        运行全部测试 (含 benchmark)"
	@echo "  make lint            代码检查"
	@echo "  make clean           清理缓存和临时文件"
	@echo "  make run             启动 Web 服务 (模拟模式)"
	@echo "  make run-cli         启动交互式 CLI"

install:
	pip install -r requirements-web.txt

test: test-unit test-integration

test-unit:
	pytest tests/unit/ -v --tb=short

test-integration:
	pytest tests/integration/ -v --tb=short

test-e2e:
	pytest tests/e2e/ -v --tb=short
	python tests/e2e/runner.py

test-benchmark:
	pytest tests/benchmark/ -v --tb=short
	python tests/benchmark/run.py

test-all: test test-e2e test-benchmark

lint:
	@echo "Checking imports..."
	@python -c "import core.config; import core.observability; import core.governance; import core.lifecycle; print('All imports OK')" 2>&1 || echo "Import check failed"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf traces/ 2>/dev/null || true
	rm -rf logs/*.log 2>/dev/null || true

run:
	python main.py --mode simulated --debug

run-cli:
	python main.py --cli

run-real:
	python main.py --mode real --debug
