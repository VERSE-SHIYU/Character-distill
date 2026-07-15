"""Backfill local user profiles to peer node."""
import sys; sys.path.insert(0, '/app/web')
import asyncio
from deps import get_storage
from cross_border_sync import forward_user_profile_to_peer

s = get_storage()

async def backfill():
    users = await s.get_all_users_admin_fields()
    ok = 0
    fail = 0
    for u in users:
        uid = u.get('id', '')
        uname = u.get('username', '')
        region = u.get('home_region', '')
        avatar = u.get('avatar_data', '')
        try:
            r = await forward_user_profile_to_peer(uid, uname, region, avatar)
            if r:
                ok += 1
                print('  OK: ' + uname)
            else:
                fail += 1
                print('  FAIL: ' + uname)
        except Exception as e:
            fail += 1
            print('  ERROR: ' + uname + ': ' + str(e))
    print('Done: %d ok, %d failed' % (ok, fail))

asyncio.run(backfill())
