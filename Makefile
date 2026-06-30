.PHONY: sync
sync:
	@echo "Syncing the package with development dependencies..."
	uv sync --extra dev
	@echo "Sync completed."

.PHONY: build
build:
	@echo "Building the package..."
	uv sync --extra dev
	bash build.sh
	@echo "Build completed."
