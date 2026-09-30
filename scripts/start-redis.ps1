param([string]$Distribution = 'Ubuntu')
$ErrorActionPreference = 'Stop'
$redisRoot = Split-Path -Parent $PSScriptRoot
$redisPython = Join-Path $redisRoot '.venv\Scripts\python.exe'
$redisSettings = Join-Path $redisRoot 'data\redis-private\application.env'
if (-not (Test-Path -LiteralPath $redisSettings)) { throw 'Redis is not configured. Follow docs/maintenance/redis-migration.md.' }
& wsl.exe -d $Distribution -u root -- systemctl start gameplan-redis.service
if ($LASTEXITCODE -ne 0) { throw 'Redis could not start in WSL.' }
& $redisPython -c "from dotenv import load_dotenv; load_dotenv(r'$redisSettings'); from gameplan.persistence.redis_store import client; assert client().ping(); assert client().info('server')['redis_version'].startswith('8.'); print('Redis 8 is ready.')"
if ($LASTEXITCODE -ne 0) { throw 'Redis health check failed.' }
