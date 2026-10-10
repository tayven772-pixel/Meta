package app.nexboost

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

private val Ink = Color(0xFF0A0D0B)
private val Panel = Color(0xFF151B17)
private val Green = Color(0xFF7CFF6B)
private val Muted = Color(0xFF9AA69D)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { NexBoostApp(this) }
    }
}

@Composable
fun NexBoostApp(context: Context) {
    var page by remember { mutableStateOf("Home") }
    var game by remember { mutableStateOf("Minecraft") }
    val prefs = remember { context.getSharedPreferences("nexboost", Context.MODE_PRIVATE) }
    val manager = context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
    val mem = ActivityManager.MemoryInfo().also { manager.getMemoryInfo(it) }
    val battery = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
    val level = battery?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
    val scale = battery?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
    val batteryPct = if (level >= 0 && scale > 0) level * 100 / scale else -1
    val usedMb = (mem.totalMem - mem.availMem) / (1024 * 1024)
    val totalMb = mem.totalMem / (1024 * 1024)
    val tasks = listOf(
        "Enable your phone's built-in Game Mode",
        "Turn on Do Not Disturb while playing",
        "Close apps you are not actively using",
        "Use a stable Wi-Fi connection",
        "Keep the phone cool and vents unobstructed",
        "Lower in-game graphics if frame drops continue"
    )
    MaterialTheme(colorScheme = darkColorScheme(primary = Green, background = Ink, surface = Panel, onBackground = Color.White, onSurface = Color.White)) {
        Scaffold(containerColor = Ink, bottomBar = {
            NavigationBar(containerColor = Panel) {
                listOf("Home", "Games", "Tools", "Settings").forEach { item ->
                    NavigationBarItem(selected = page == item, onClick = { page = item }, icon = {
                        Icon(when(item) { "Games" -> Icons.Default.SportsEsports; "Tools" -> Icons.Default.Tune; "Settings" -> Icons.Default.Settings; else -> Icons.Default.Dashboard }, contentDescription = item)
                    }, label = { Text(item) }, colors = NavigationBarItemDefaults.colors(selectedIconColor = Green, selectedTextColor = Green, indicatorColor = Color(0xFF263A27), unselectedIconColor = Muted, unselectedTextColor = Muted))
                }
            }
        }) { padding ->
            LazyColumn(modifier = Modifier.fillMaxSize().padding(padding).padding(horizontal = 18.dp), verticalArrangement = Arrangement.spacedBy(14.dp), contentPadding = PaddingValues(top = 22.dp, bottom = 24.dp)) {
                item {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(Modifier.size(42.dp).background(Green, RoundedCornerShape(13.dp)), contentAlignment = Alignment.Center) { Icon(Icons.Default.Bolt, null, tint = Ink, modifier = Modifier.size(27.dp)) }
                        Spacer(Modifier.width(10.dp))
                        Column { Text("NEXBOOST", fontSize = 21.sp, fontWeight = FontWeight.Black, letterSpacing = 2.sp); Text("ANDROID GAMING TOOLKIT", color = Muted, fontSize = 10.sp, letterSpacing = 1.5.sp) }
                        Spacer(Modifier.weight(1f))
                        Surface(color = Color(0xFF1B3020), shape = RoundedCornerShape(20.dp)) { Text("● READY", color = Green, fontSize = 10.sp, modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp)) }
                    }
                }
                when (page) {
                    "Home" -> {
                        item { Column { Text("Play at your best.", fontSize = 28.sp, fontWeight = FontWeight.Bold); Text("Useful device insights. No fake boosts.", color = Muted) } }
                        item { Card(colors = CardDefaults.cardColors(containerColor = Panel), shape = RoundedCornerShape(22.dp)) { Column(Modifier.padding(18.dp)) {
                            Text("YOUR DEVICE", color = Green, fontSize = 11.sp, letterSpacing = 1.5.sp)
                            Spacer(Modifier.height(8.dp)); Text(Build.MANUFACTURER.replaceFirstChar { it.uppercase() } + " " + Build.MODEL, fontSize = 20.sp, fontWeight = FontWeight.Bold)
                            Text("Android " + Build.VERSION.RELEASE + " • API " + Build.VERSION.SDK_INT, color = Muted, fontSize = 12.sp)
                            Spacer(Modifier.height(18.dp))
                            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                                Metric("MEMORY IN USE", "$usedMb MB", "of $totalMb MB", Modifier.weight(1f))
                                Metric("BATTERY", if (batteryPct >= 0) "$batteryPct%" else "Unavailable", "system reading", Modifier.weight(1f))
                            }
                            Spacer(Modifier.height(10.dp)); Text("Memory values describe system memory at this moment, not RAM that NexBoost can free.", color = Muted, fontSize = 11.sp)
                        } } }
                        item { Text("QUICK START", fontWeight = FontWeight.Bold, fontSize = 16.sp) }
                        item { ActionCard("Game-ready checklist", "Six practical changes you can apply safely", Icons.Default.CheckCircle, Green) { page = "Tools" } }
                        item { ActionCard("Choose a game profile", "Get tailored settings guidance", Icons.Default.SportsEsports, Green) { page = "Games" } }
                        item { Text("HONEST PERFORMANCE", color = Green, fontSize = 11.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.sp) }
                        item { Text("Android does not let ordinary apps force higher FPS, clear other apps' RAM, or control CPU/GPU governors. NexBoost provides real system readings and actionable guidance instead.", color = Muted, fontSize = 13.sp) }
                    }
                    "Games" -> {
                        item { Text("Game profiles", fontSize = 27.sp, fontWeight = FontWeight.Bold) }
                        item { Text("Pick a game for relevant in-game suggestions. Profiles do not change game settings automatically.", color = Muted) }
                        item { Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) { listOf("Minecraft", "Roblox", "Other").forEach { g -> FilterChip(selected = game == g, onClick = { game = g }, label = { Text(g) }, leadingIcon = if (game == g) ({ Icon(Icons.Default.Check, null) }) else null) } } }
                        item { Card(colors = CardDefaults.cardColors(containerColor = Panel), shape = RoundedCornerShape(20.dp)) { Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                            Text(game.uppercase(), color = Green, fontSize = 12.sp, letterSpacing = 1.5.sp)
                            Text(when(game) { "Minecraft" -> "Try a lower render distance, reduce fancy graphics and particles, and use a stable frame-rate target."; "Roblox" -> "Lower the in-game graphics quality if the experience stutters, and close overlays you do not need."; else -> "Start with the game's own performance settings and your phone's built-in gaming mode." }, fontSize = 15.sp)
                            HorizontalDivider(color = Color(0xFF2A342D))
                            Text("Recommended routine", fontWeight = FontWeight.Bold)
                            Text("1. Enable the phone's gaming mode if available.\n2. Avoid charging in a hot environment.\n3. Test one setting at a time.", color = Muted, fontSize = 13.sp)
                        } } }
                    }
                    "Tools" -> {
                        item { Text("Optimization checklist", fontSize = 27.sp, fontWeight = FontWeight.Bold) }
                        item { Text("These are user-applied recommendations, not hidden system tweaks. Your progress is saved on this device.", color = Muted) }
                        items(tasks) { task ->
                            val index = tasks.indexOf(task)
                            val key = "task_$index"
                            var checked by remember { mutableStateOf(prefs.getBoolean(key, false)) }
                            Card(colors = CardDefaults.cardColors(containerColor = Panel), shape = RoundedCornerShape(15.dp)) {
                                Row(Modifier.fillMaxWidth().padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                                    Checkbox(checked = checked, onCheckedChange = { checked = it; prefs.edit().putBoolean(key, it).apply() }, colors = CheckboxDefaults.colors(checkedColor = Green, checkmarkColor = Ink))
                                    Text(task, Modifier.weight(1f), fontSize = 14.sp)
                                }
                            }
                        }
                        item { OutlinedButton(onClick = { tasks.indices.forEach { prefs.edit().putBoolean("task_$it", false).apply() } }, modifier = Modifier.fillMaxWidth()) { Text("Reset checklist") } }
                        item { Text("Not available to ordinary apps: one-tap RAM cleaning, CPU/GPU overclocking, thermal bypass, guaranteed FPS gains.", color = Muted, fontSize = 12.sp) }
                    }
                    else -> {
                        item { Text("Settings & transparency", fontSize = 27.sp, fontWeight = FontWeight.Bold) }
                        item { Card(colors = CardDefaults.cardColors(containerColor = Panel)) { Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                            Text("What NexBoost actually does", color = Green, fontWeight = FontWeight.Bold)
                            Text("• Reads available system memory and battery level from Android APIs.\n• Reads device model and Android version.\n• Saves your checklist locally on this device.\n• Offers game-specific recommendations.")
                            HorizontalDivider(color = Color(0xFF2A342D))
                            Text("What it does not do", color = Green, fontWeight = FontWeight.Bold)
                            Text("• No fake FPS or benchmark numbers.\n• No remote telemetry or account required.\n• No root access or protected settings changes.\n• No promise of performance gains.")
                        } } }
                        item { OutlinedButton(onClick = { page = "Home" }, modifier = Modifier.fillMaxWidth()) { Text("Back to dashboard") } }
                    }
                }
            }
        }
    }
}

@Composable
private fun Metric(title: String, value: String, detail: String, modifier: Modifier = Modifier) {
    Card(modifier = modifier, colors = CardDefaults.cardColors(containerColor = Color(0xFF202922)), shape = RoundedCornerShape(16.dp)) {
        Column(Modifier.padding(12.dp)) {
            Text(title, color = Muted, fontSize = 9.sp, letterSpacing = 1.sp)
            Spacer(Modifier.height(8.dp)); Text(value, fontSize = 19.sp, fontWeight = FontWeight.Bold, color = Green)
            Text(detail, color = Muted, fontSize = 10.sp)
        }
    }
}

@Composable
private fun ActionCard(title: String, detail: String, icon: androidx.compose.ui.graphics.vector.ImageVector, accent: Color, action: () -> Unit) {
    Card(onClick = action, colors = CardDefaults.cardColors(containerColor = Panel), shape = RoundedCornerShape(16.dp)) {
        Row(Modifier.fillMaxWidth().padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(icon, null, tint = accent, modifier = Modifier.size(28.dp))
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) { Text(title, fontWeight = FontWeight.Bold); Text(detail, color = Muted, fontSize = 12.sp) }
            Icon(Icons.Default.ChevronRight, null, tint = Muted)
        }
    }
}
