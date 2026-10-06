# Auto test plan

CI (`.github/workflows/ci.yml`) runs `pytest` on every push and pull request. Real ROM tests skip unless `GAME_BRAIN_ROM` and mGBA bindings exist.

| Area | Test | CI |
|---|---|---|
| Arbiter modes | `tests/test_arbiter.py` | yes |
| PathBrain, milestones, stuck | `test_path_brain.py`, `test_nav.py`, `test_stuck.py` | yes |
| Rule / Random | `test_brains.py` | yes |
| LLM stub fallback | `test_brains.py` (`test_llm_brain_is_unavailable`) | yes |
| RuleBattleBrain | `test_battle_brain.py` | yes |
| Real battle / starter / whiteout | `test_*_real.py` | skip without ROM |
| Imitation | `test_imitation_brain.py` | yes |
| Go-Explore | `test_go_explore.py` | yes |
| Progress score / short runner | `test_short_rl.py`, `test_rl_env_reset.py` | yes |
| Map + battle learner | `test_learn_demo.py` | yes |
| Action-weight baseline | `test_ppo_baseline.py` | yes |
| Real PPO on FireRed, LLM subgoals, Pokedex RAG | none | not implemented |
