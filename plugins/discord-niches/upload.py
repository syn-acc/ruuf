"""Upload the seven approved SCRU niche images; persist progress after each upload."""
import asyncio
import json
from pathlib import Path
import discord
from dotenv import dotenv_values

HOME=Path('/data/syn.gl/hermes/.hermes')
ROOT=Path('/data/syn.gl/hermes/plugins/discord-niches')
CONFIG=HOME/'niche-roles.json'

async def main():
 data=json.loads(CONFIG.read_text())
 assert data['guild_id']==1429742642505191556
 async with discord.Client(intents=discord.Intents.none()) as c:
  await c.login(dotenv_values(HOME/'.env')['DISCORD_BOT_TOKEN'])
  guild=await c.fetch_guild(data['guild_id'])
  emojis=await guild.fetch_emojis()
  names={'chinese':'scru_chinese','rationalism':'scru_rationalism','seek-discomfort':'scru_seek_discomfort','mech-interp':'scru_mech_interp','chess':'scru_chess','agent-swarms-minecraft':'scru_swarms','league':'scru_league'}
  for g in data['groups']:
   name=names[g['key']]
   found=[e for e in emojis if e.name==name]
   assert len(found)<=1
   if found:
    emoji=found[0]
   else:
    emoji=await guild.create_custom_emoji(name=name,image=(ROOT/'emojis'/(g['key']+'.png')).read_bytes(),reason='Syn approved SCRU synthwave niche reaction icons')
    emojis.append(emoji)
   assert not emoji.animated and not emoji.roles
   g['emoji']=str(emoji)
   g['emoji_id']=emoji.id
   temp=CONFIG.with_suffix('.tmp')
   temp.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')
   temp.chmod(0o600)
   temp.replace(CONFIG)
  actual={e.id:e for e in await guild.fetch_emojis()}
  assert all(g['emoji_id'] in actual for g in data['groups'])
  print(json.dumps({'uploaded':[{'key':g['key'],'emoji':g['emoji']} for g in data['groups']],'message_id':data['message_id'],'verified':True}))
asyncio.run(main())
