import http from 'node:http'
import { spawn } from 'node:child_process'
import { Client, GatewayIntentBits, ChannelType, PermissionFlagsBits } from 'discord.js'
import { joinVoiceChannel, createAudioPlayer, createAudioResource, AudioPlayerStatus, VoiceConnectionStatus, StreamType, entersState, NoSubscriberBehavior } from '@discordjs/voice'
import googleTTS from 'google-tts-api'
import ffmpegPath from 'ffmpeg-static'

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

const balances = new Map()
const dailyClaims = new Map()
const rewardRules = []

const commands = [
  { name: 'help', description: 'Show Meta Support commands' },
  { name: 'intro', description: 'Post the Meta Support intro panel', default_member_permissions: String(PermissionFlagsBits.ManageGuild) },
  { name: 'invites', description: 'Check invite stats', options: [{ type: 6, name: 'user', description: 'User to check', required: false }] },
  { name: 'leaderboard', description: 'Show the invite leaderboard' },
  { name: 'balance', description: 'Check a coin balance', options: [{ type: 6, name: 'user', description: 'User to check', required: false }] },
  { name: 'daily', description: 'Claim your daily coin reward' },
  {
    name: 'giveaway',
    description: 'Giveaway tools',
    default_member_permissions: String(PermissionFlagsBits.ManageGuild),
    options: [{
      type: 1,
      name: 'start',
      description: 'Start a giveaway',
      options: [
        { type: 3, name: 'prize', description: 'Prize name', required: true },
        { type: 4, name: 'minutes', description: 'Duration in minutes', required: true, min_value: 1, max_value: 10080 },
        { type: 4, name: 'winners', description: 'Number of winners', required: false, min_value: 1, max_value: 20 },
      ],
    }],
  },
  {
    name: 'reward',
    description: 'Invite reward tools',
    default_member_permissions: String(PermissionFlagsBits.ManageRoles),
    options: [
      {
        type: 1,
        name: 'add',
        description: 'Add an invite reward role',
        options: [
          { type: 4, name: 'invites', description: 'Invites required', required: true, min_value: 1 },
          { type: 8, name: 'role', description: 'Role to give', required: true },
          { type: 3, name: 'label', description: 'Reward name', required: false },
        ],
      },
      { type: 1, name: 'list', description: 'List invite rewards' },
    ],
  },
  { name: 'serverstats', description: 'Show live server stats' },
]

async function pickIntroChannel(guild) {
  if (process.env.INTRO_CHANNEL_ID) {
    const configured = guild.channels.cache.get(process.env.INTRO_CHANNEL_ID)
    if (configured?.isTextBased()) return configured
  }
  const system = guild.systemChannel
  if (system?.isTextBased()) return system
  return guild.channels.cache.find(
    c => c.type === ChannelType.GuildText && c.permissionsFor(guild.members.me)?.has(PermissionFlagsBits.SendMessages)
  ) ?? null
}

function introPayload(userId, guildName) {
  return {
    content: userId
      ? `Welcome <@${userId}> to **${guildName}**! Welcome to Meta. Use /help to see everything I can do.`
      : `Welcome to **${guildName}**! Meta Support is online.`,
    embeds: [{
      title: 'Welcome to Meta',
      description: 'Meta Support is active here. Use /help to see commands, /invites for invite stats, /daily for coins, and /leaderboard for rankings.',
      color: 0x5970db,
      footer: { text: 'Meta Support • meta.floot.app' },
    }],
    allowedMentions: { users: userId ? [userId] : [] },
  }
}

async function pickIntroVoiceChannel(guild, preferredChannel = null) {
  if (preferredChannel?.type === ChannelType.GuildVoice) return preferredChannel

  if (process.env.INTRO_VOICE_CHANNEL_ID) {
    const configured = guild.channels.cache.get(process.env.INTRO_VOICE_CHANNEL_ID)
    if (configured?.type === ChannelType.GuildVoice) return configured
  }

  const me = guild.members.me
  return guild.channels.cache.find(channel =>
    channel.type === ChannelType.GuildVoice &&
    me &&
    channel.permissionsFor(me)?.has(PermissionFlagsBits.Connect) &&
    channel.permissionsFor(me)?.has(PermissionFlagsBits.Speak)
  ) ?? null
}

async function speakIntroInVoice(guild, preferredChannel = null) {
  const voiceChannel = await pickIntroVoiceChannel(guild, preferredChannel)
  if (!voiceChannel) throw new Error('No voice channel available for the intro')

  const connection = joinVoiceChannel({
    channelId: voiceChannel.id,
    guildId: guild.id,
    adapterCreator: guild.voiceAdapterCreator,
    selfDeaf: true,
    selfMute: false,
    daveEncryption: false,
    debug: true,
  })

  connection.on('debug', message => console.log('[VOICE]', message))
  connection.on('stateChange', (oldState, newState) => {
    console.log('[VOICE STATE]', oldState.status, '->', newState.status)
  })

  try {
    await entersState(connection, VoiceConnectionStatus.Ready, 15_000)

    const speech = 'Welcome to Meta. Meta Support is online. Use slash help to see everything I can do.'
    const url = googleTTS.getAudioUrl(speech, { lang: 'en', slow: false, host: 'https://translate.google.com' })

    if (!ffmpegPath) throw new Error('FFmpeg is unavailable')
    const ffmpeg = spawn(ffmpegPath, [
      '-loglevel', 'error',
      '-i', url,
      '-f', 's16le',
      '-ar', '48000',
      '-ac', '2',
      'pipe:1',
    ], { stdio: ['ignore', 'pipe', 'pipe'] })

    let ffmpegError = ''
    ffmpeg.stderr.on('data', chunk => { ffmpegError += chunk.toString() })

    const player = createAudioPlayer({
      behaviors: { noSubscriber: NoSubscriberBehavior.Play },
    })
    const resource = createAudioResource(ffmpeg.stdout, { inputType: StreamType.Raw })
    connection.subscribe(player)

    const finished = new Promise((resolve, reject) => {
      player.once(AudioPlayerStatus.Idle, resolve)
      player.once('error', reject)
      ffmpeg.once('error', reject)
      ffmpeg.once('close', code => {
        if (code && code !== 0) reject(new Error('FFmpeg exited with code ' + code + ': ' + ffmpegError.slice(-300)))
      })
    })

    player.play(resource)
    await finished
    console.log('Voice intro played in channel=' + voiceChannel.id)
  } finally {
    connection.destroy()
  }
}

async function sendIntro(member) {
  const channel = await pickIntroChannel(member.guild)
  if (channel) await channel.send(introPayload(member.id, member.guild.name))
  await speakIntroInVoice(member.guild).catch(error => console.error('Automatic voice intro failed:', error))
}

async function configureDiscordApp() {
  const guild = client.guilds.cache.get(GUILD_ID)
  if (!guild) throw new Error('Target guild not found')

  await guild.commands.set(commands)

  const appId = client.application?.id
  if (appId) {
    const response = await fetch('https://discord.com/api/v10/applications/' + appId, {
      method: 'PATCH',
      headers: {
        Authorization: 'Bot ' + TOKEN,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ interactions_endpoint_url: null }),
    })
    console.log('Interactions endpoint cleared:', response.status)
  }
}

client.once('clientReady', async () => {
  console.log(`Logged in as ${client.user.tag}`)
  const guild = client.guilds.cache.get(GUILD_ID)
  console.log(guild ? `Target guild ready: ${guild.name} (${guild.id})` : `Target guild ${GUILD_ID} not found`)
  try {
    await configureDiscordApp()
    console.log('Guild commands registered directly on Render')
  } catch (error) {
    console.error('Discord app setup failed:', error)
  }
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

client.on('interactionCreate', async interaction => {
  if (!interaction.isChatInputCommand()) return
  if (interaction.guildId !== GUILD_ID) {
    await interaction.reply({ content: 'Meta Support is currently configured for the Meta server only.', ephemeral: true })
    return
  }

  try {
    if (interaction.commandName === 'help') {
      await interaction.reply({
        content: '**Meta Support commands**\n/intro — post the welcome intro\n/invites — invite stats\n/leaderboard — top inviters\n/balance — coin balance\n/daily — daily coins\n/giveaway start — start a giveaway\n/reward add or /reward list — invite rewards\n/serverstats — live server stats',
      })
      return
    }

    if (interaction.commandName === 'intro') {
      const member = interaction.member
      const preferred = member?.voice?.channel ?? null
      await interaction.deferReply()
      try {
        await speakIntroInVoice(interaction.guild, preferred)
        await interaction.editReply({ content: preferred ? `Played the intro in **${preferred.name}**.` : 'Played the intro in the configured voice channel.' })
      } catch (error) {
        console.error('Manual voice intro failed:', error)
        await interaction.editReply({ content: 'I could not play the voice intro. Join a voice channel first, or configure an intro voice channel.' })
      }
      return
    }

    if (interaction.commandName === 'serverstats') {
      await interaction.guild.members.fetch()
      await interaction.reply({
        content: `**${interaction.guild.name}**\nMembers: **${interaction.guild.memberCount}**\nChannels: **${interaction.guild.channels.cache.size}**\nRoles: **${interaction.guild.roles.cache.size}**`,
      })
      return
    }

    if (interaction.commandName === 'giveaway') {
      const prize = interaction.options.getString('prize', true)
      const minutes = interaction.options.getInteger('minutes', true)
      const winners = interaction.options.getInteger('winners') ?? 1
      const endsAt = Date.now() + minutes * 60_000
      await interaction.reply({
        content: `🎉 **GIVEAWAY STARTED**\nPrize: **${prize}**\nWinners: **${winners}**\nEnds <t:${Math.floor(endsAt / 1000)}:R>`,
      })
      return
    }

    if (interaction.commandName === 'daily') {
      const userId = interaction.user.id
      const last = dailyClaims.get(userId) ?? 0
      const now = Date.now()
      const day = 24 * 60 * 60 * 1000
      if (now - last < day) {
        const hours = Math.ceil((day - (now - last)) / 3600000)
        await interaction.reply({ content: `You already claimed your daily reward. Try again in about **${hours} hour(s)**.`, ephemeral: true })
        return
      }
      const reward = 100
      dailyClaims.set(userId, now)
      balances.set(userId, (balances.get(userId) ?? 0) + reward)
      await interaction.reply({ content: `You claimed **${reward} coins**. Your balance is now **${balances.get(userId)}**.` })
      return
    }

    if (interaction.commandName === 'balance') {
      const target = interaction.options.getUser('user') ?? interaction.user
      await interaction.reply({ content: `<@${target.id}> has **${balances.get(target.id) ?? 0} coins**.` })
      return
    }

    if (interaction.commandName === 'invites') {
      const target = interaction.options.getUser('user') ?? interaction.user
      await interaction.reply({ content: `<@${target.id}> currently has **0 tracked invites**. Live invite attribution is the next system being connected.` })
      return
    }

    if (interaction.commandName === 'leaderboard') {
      await interaction.reply({ content: 'No tracked invite data yet.' })
      return
    }

    if (interaction.commandName === 'reward') {
      const sub = interaction.options.getSubcommand()
      if (sub === 'add') {
        const invites = interaction.options.getInteger('invites', true)
        const role = interaction.options.getRole('role', true)
        const label = interaction.options.getString('label') ?? `${invites} Invite Reward`
        rewardRules.push({ invites, roleId: role.id, label })
        await interaction.reply({ content: `Added **${label}** at **${invites} invites** for <@&${role.id}>.` })
        return
      }
      if (sub === 'list') {
        if (!rewardRules.length) {
          await interaction.reply({ content: 'No invite rewards are configured yet.' })
          return
        }
        await interaction.reply({
          content: '**Invite rewards**\n' + rewardRules.map(r => `**${r.invites} invites** — ${r.label} (<@&${r.roleId}>)`).join('\n'),
        })
        return
      }
    }

    await interaction.reply({ content: 'That command is not available yet.', ephemeral: true })
  } catch (error) {
    console.error('Interaction failed:', error)
    if (interaction.replied || interaction.deferred) {
      await interaction.followUp({ content: 'Something went wrong while running that command.', ephemeral: true }).catch(() => {})
    } else {
      await interaction.reply({ content: 'Something went wrong while running that command.', ephemeral: true }).catch(() => {})
    }
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
