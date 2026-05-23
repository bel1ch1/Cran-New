#!/usr/bin/env python3
"""Supervisor entrypoint for hook_pose_modbus.py."""

from __future__ import annotations

from app.services.pose_supervisor import SupervisorRole, run_pose_supervisor


if __name__ == "__main__":
    raise SystemExit(run_pose_supervisor(SupervisorRole.HOOK))
