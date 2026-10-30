PY ?= python
TF_DIR := infra/terraform
LAKE ?= data/lake
START ?= 2026-10-01
END ?= 2026-10-07

.PHONY: install test lint demo clean package tf-init tf-plan tf-apply tf-destroy upload-sample athena-views

install:
	$(PY) -m pip install -e ".[dev]"

test:
	$(PY) -m pytest -q

lint:
	ruff check .

# Seven days of synthetic data through the full pipeline, then every analytics query on DuckDB.
demo:
	$(PY) -m finpipe.cli --lake $(LAKE) backfill --start $(START) --end $(END) --rows 5000
	$(PY) -m finpipe.cli --lake $(LAKE) summary
	@for q in 01 02 04 06 07; do $(PY) -m finpipe.cli --lake $(LAKE) query $$q; done

clean:
	rm -rf data build dist .pytest_cache .ruff_cache

package:
	$(PY) scripts/build_artifacts.py

tf-init:
	cd $(TF_DIR) && terraform init

tf-plan: package
	cd $(TF_DIR) && terraform plan

tf-apply: package
	cd $(TF_DIR) && terraform apply

tf-destroy:
	cd $(TF_DIR) && terraform destroy

upload-sample:
	$(PY) scripts/upload_sample_data.py --bucket $$(cd $(TF_DIR) && terraform output -raw bucket_name) --start $(START) --end $(END)

athena-views:
	$(PY) scripts/apply_athena_views.py --database $$(cd $(TF_DIR) && terraform output -raw glue_database) --workgroup $$(cd $(TF_DIR) && terraform output -raw athena_workgroup)
