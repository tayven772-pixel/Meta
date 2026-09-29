import http from 'node:http'
import { Client, GatewayIntentBits, ChannelType } from 'discord.js'

const TOKEN = process.env.DISCORD_BOT_TOKEN
const GUILD_ID = process.env.DISCORD_GUILD_ID || '1554300458409922590'
const PORT = Number(process.env.PORT || 10000)

if (!TOKEN) {
  console.error('DISCORD_BOT_TOKEN is required')
  process.exit(1)
}

const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMembers,
    GatewayIntentBits.GuildVoiceStates,
  ],
})

async function pickIntroChannel(guild) {
  if (process.env.INTRO_CHANNEL_ID) {
    const configured = guild.channels.cache.get(process.env.INTRO_CHANNEL_ID)
    if (configured?.isTextBased()) return configured
  }
  const system = guild.systemChannel
  if (system?.isTextBased()) return system
  return guild.channels.cache.find(
    c => c.type === ChannelType.GuildText && c.permissionsFor(guild.members.me)?.has('SendMessages')
  ) ?? null
}

async function sendIntro(member) {
  const channel = await pickIntroChannel(member.guild)
  if (!channel) return

  await channel.send({
    content: `Welcome <@${member.id}> to **${member.guild.name}**! Welcome to Meta. Use slash help to see everything I can do.`,
    tts: true,
    embeds: [{
      title: 'Welcome to Meta',
      description: 'Meta Support is active here. Use /help to see commands, /invites for invite stats, /daily for coins, and /leaderboard for rankings.',
      color: 0x5970db,
      footer: { text: 'Meta Support • meta.floot.app' },
    }],
    allowedMentions: { users: [member.id] },
  })
}

client.once('ready', async () => {
  console.log(`Logged in as ${client.user.tag}`)
  const guild = client.guilds.cache.get(GUILD_ID)
  console.log(guild ? `Target guild ready: ${guild.name} (${guild.id})` : `Target guild ${GUILD_ID} not found`)
})

client.on('guildMemberAdd', async member => {
  if (member.guild.id !== GUILD_ID) return
  try {
    await sendIntro(member)
    console.log(`Intro sent for ${member.user.tag}`)
  } catch (error) {
    console.error('Failed to send intro:', error)
  }
})

client.on('error', error => console.error('Discord client error:', error))

http.createServer((req, res) => {
  res.writeHead(200, { 'content-type': 'application/json' })
  res.end(JSON.stringify({
    ok: true,
    bot: client.user?.tag ?? null,
    ready: client.isReady(),
    guildId: GUILD_ID,
  }))
}).listen(PORT, '0.0.0.0', () => {
  console.log(`Health server listening on ${PORT}`)
})

await client.login(TOKEN)
