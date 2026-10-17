"""Run Telegram + Bale with one dispatcher, shared inventory, disjoint identities."""
import asyncio
import argparse
from main import main

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Shared RAINOTEK Telegram/Bale runtime')
    parser.add_argument('--direct-network', action='store_true', help='Use direct API connections for this process only')
    args = parser.parse_args()
    if args.direct_network:
        from bot.config import get_settings
        get_settings().NETWORK_PROXY_URL = ''
    asyncio.run(main(enable_bale=True))
