# experiments/Makefile — the rig's front door.
# From repo root: make -C experiments up   (make -C chdir's here, so all
# paths below are relative to experiments/; scripts themselves cd to the
# repo root).
up:
	bash cluster/create_cluster.sh
	bash cluster/install_stack.sh
	kubectl apply -f ../experiments/sim/
run:
	bash repro.sh $(ARM) $(PATTERN) $(SEED)
reset:
	-kubectl -n serving delete scaledobject --all
	kubectl -n serving scale deploy/llm-sim --replicas=1
down:
	k3d cluster delete sigscale

NIGHTS = night1 night2 night3 night4
$(NIGHTS):
	cd .. && .venv/bin/python -m src.batch_runner experiments/batches/$@.tsv
qa:
	cd .. && .venv/bin/python -m src.agg_results qa
summary:
	cd .. && .venv/bin/python -m src.agg_results summary --out results/summary.csv
	cd .. && .venv/bin/python -m src.agg_results variance --out results/variance.csv
.PHONY: $(NIGHTS) qa summary
