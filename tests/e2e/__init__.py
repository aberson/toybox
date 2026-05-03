"""Full-system end-to-end smoke tests.

These tests boot real backend + real frontend subprocesses and drive
them via Playwright. They are marked ``@pytest.mark.slow`` and are
excluded from the default pytest run; the build-step orchestrator runs
them as the evidence step. The fast suite under ``tests/integration/``
covers per-component contracts without subprocesses.
"""
