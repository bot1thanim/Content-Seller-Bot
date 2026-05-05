# Workspace

## Overview

pnpm workspace monorepo using TypeScript. Each package manages its own dependencies.
Includes a Python Telegram bot for content management and sales.

## Stack

- **Monorepo tool**: pnpm workspaces
- **Node.js version**: 24
- **Package manager**: pnpm
- **TypeScript version**: 5.9
- **API framework**: Express 5
- **Database**: PostgreSQL + Drizzle ORM
- **Validation**: Zod (`zod/v4`), `drizzle-zod`
- **API codegen**: Orval (from OpenAPI spec)
- **Build**: esbuild (CJS bundle)
- **Python**: 3.11 (for Telegram bot)
- **Telegram**: python-telegram-bot 20.7

## Key Commands

- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- `pnpm --filter @workspace/api-server run dev` — run API server locally

See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details.

## Telegram Bot (`telegram-bot/`)

Full-featured Telegram bot for content (video) management and sales.

### Files
- `telegram-bot/bot.py` — main bot logic
- `telegram-bot/requirements.txt` — Python dependencies
- `telegram-bot/data/` — JSON data storage (auto-created on first run)
  - `users.json` — registered users
  - `coins.json` — coin balances
  - `referrals.json` — referral tracking
  - `videos.json` — video file_ids storage
  - `orders.json` — purchase orders

### Environment Variables
- `TELEGRAM_BOT_TOKEN` — bot token from BotFather
- `ADMIN_ID` — Telegram user ID of the admin (7706183809)

### Workflow
- Name: `Telegram Bot`
- Command: `cd telegram-bot && python bot.py`

### Features
- User UI: PayPal packages (8 tiers), referral system, coin wallet, support
- Admin panel (reply keyboard only for admin): stats, orders, user check, send message, approve payment + send videos, video list, broadcast, coin management, backup, delete all videos
- Auto-save videos: admin sends video → saved to data/videos.json
- Referral system: permanent coins (1 coin per referral)
- All text in Hebrew
