# TotalMix OSC Bridge

MQTT → OSC bridge for RME TotalMix FX.  
Allows Home Assistant (or any MQTT client) to control TotalMix workspaces, snapshots, and (in the future) external FX sends.

## Features
- Load Quick Workspaces (1–30)
- Load Snapshots (1–8)
- Easy configuration via `.env`
- Designed to run in Docker

## Setup
1. Copy `.env.example` to `.env` and fill in your values
2. Build and run with Docker Compose

## Environment Variables
See `.env.example`