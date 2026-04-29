# NoteCast Development Plan

## Completed Features ✅

### 1. NotebookLM Token Expiry Tracking
- [x] Extract token expiry from storage_state.json cookies
- [x] Expose token expiry via /api/status endpoint
- [x] Display token countdown in admin panel with warning colors
- [x] Send ntfy notifications when token approaches expiry
- [x] Make notification threshold configurable (TOKEN_EXPIRY_WARN_DAYS env var)
- [x] Prevent notification spam (max once per 24h)

### 2. Git Cleanup
- [x] Remove .spec-gen/ from git tracking (keep locally)
- [x] Remove .claude/ from git tracking (keep locally)
- [x] Remove bridge/history.json from git tracking
- [x] Add ARCHITECTURE.md to .gitignore

### 3. Asset Optimization
- [x] Rescale cover.png to 1400x1400

### 4. RSS Transformer Multi-User Support

#### Phase 1: Config & User Loading ✅
- [x] Import/define User dataclass with per-user storage
- [x] Implement multi-user config loading (USERS env var)
- [x] Create per-user database files at /data/{user}/transformer.db

#### Phase 2: Database & Storage ✅
- [x] Add user_name column to jobs table
- [x] Update database indexes for user context
- [x] Organize episodes/feeds: /public/episodes/{user}/, /public/feed/{user}/
- [x] Generate and persist per-user feed tokens

#### Phase 3: Core Logic ✅
- [x] Update poll_feeds() to iterate per-user
- [x] Update job creation/retrieval for user context
- [x] Load NotebookLM client per-user auth_file

#### Phase 4: API & Output ✅
- [x] Update rebuild_feed() for per-user RSS output
- [x] Update main_async() to handle all users
- [ ] Test multi-user configuration

## Architecture

### Multi-User Support
- **Harvester** (main NoteCast): Per-user notebooks, episodes, feeds, auth
- **RSS Transformer**: Per-user RSS feed processing with isolated jobs database
- **User Model**: Standardized User dataclass across both services
- **Storage**: /public/episodes/{user}/, /public/feed/{user}/, /data/{user}/

### Authentication
- Per-user NotebookLM credentials: /root/.notebooklm/{user}/storage_state.json
- Per-user feed tokens for public access
- Optional per-user webhook configuration

### Configuration
Environment variables for multi-user setup:
```
USERS=alice,bob                    # Comma-separated user list
USER_ALICE_EMAIL=alice@example.com
USER_BOB_EMAIL=bob@example.com
USER_ALICE_WEBHOOK_URL=https://ntfy.sh/...
```

## Future Enhancements (Not Started)

- [ ] Web UI for multi-user management
- [ ] User provisioning/deprovisioning API
- [ ] Per-user quota limits
- [ ] Cross-user feed sharing
- [ ] User preferences/settings storage

## Git History

- `eb1c020` - Add NotebookLM token expiry tracking and notifications
- `6a4930c` - Rescale cover.png to 1400x1400
- `279d8cd` - Add ARCHITECTURE.md to .gitignore
- `762772f` - Remove ARCHITECTURE.md from git tracking
- `dd0fd1b` - Remove bridge/history.json from git tracking
- `bbb6472` - Remove .gitignored files from git tracking
- `f9984f7` - Add multi-user support to RSS transformer (Phase 1-4)
