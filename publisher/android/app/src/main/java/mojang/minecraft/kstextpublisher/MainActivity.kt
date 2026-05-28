package mojang.minecraft.kstextpublisher

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    val vm: PublisherViewModel = viewModel()
                    val state by vm.uiState.collectAsState()
                    PublisherScreen(state, vm)
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun PublisherScreen(
    state: PublisherUiState,
    vm: PublisherViewModel,
) {
    val selected = state.packs.firstOrNull { it.pathName == state.selectedPackPathName }

    if (state.showCreatePackDialog) {
        AlertDialog(
            onDismissRequest = { vm.showCreatePackDialog(false) },
            title = { Text("Create pack") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    OutlinedTextField(
                        value = state.newPackFileStem,
                        onValueChange = { vm.updateCreatePackFields(fileStem = it) },
                        modifier = Modifier.fillMaxWidth(),
                        label = { Text("File name / ID") },
                    )
                    OutlinedTextField(
                        value = state.newPackDisplayName,
                        onValueChange = { vm.updateCreatePackFields(displayName = it) },
                        modifier = Modifier.fillMaxWidth(),
                        label = { Text("Display name") },
                    )
                }
            },
            confirmButton = {
                TextButton(onClick = vm::createPack) {
                    Text("Create")
                }
            },
            dismissButton = {
                TextButton(onClick = { vm.showCreatePackDialog(false) }) {
                    Text("Cancel")
                }
            },
        )
    }

    if (state.showTokenHelpDialog) {
        AlertDialog(
            onDismissRequest = { vm.showTokenHelp(false) },
            title = { Text("GitHub token") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Open GitHub in a browser and create a Personal Access Token.")
                    Text("Classic token path: Settings > Developer settings > Personal access tokens > Tokens classic > Generate new token.")
                    Text("Fine-grained token path: Settings > Developer settings > Personal access tokens > Fine-grained tokens.")
                    Text("Required access: target repository, Contents read/write.")
                    Text("Paste the token into the GitHub token field, then tap Confirm login.")
                }
            },
            confirmButton = {
                TextButton(onClick = { vm.showTokenHelp(false) }) {
                    Text("OK")
                }
            },
        )
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("KsText Publisher Android", fontWeight = FontWeight.SemiBold)
                        Text(
                            state.status,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            )
        }
    ) { innerPadding ->
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
                .padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
            contentPadding = PaddingValues(bottom = 16.dp),
        ) {
            item { LoginGuideCard() }
            item { ConfigCard(state, vm) }
            item { ActionRow(state, vm) }
            item {
                Surface(
                    modifier = Modifier
                        .fillMaxWidth(),
                    shape = RoundedCornerShape(20.dp),
                    tonalElevation = 2.dp,
                ) {
                    Column(modifier = Modifier.fillMaxWidth()) {
                        Text(
                            text = state.summary,
                            modifier = Modifier.padding(16.dp),
                            style = MaterialTheme.typography.titleMedium,
                        )
                    }
                }
            }
            items(
                items = state.packs,
                key = { pack -> pack.pathName },
            ) { pack ->
                Surface(
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(16.dp),
                    tonalElevation = if (pack.pathName == state.selectedPackPathName) 3.dp else 1.dp,
                ) {
                    PackListItem(
                        pack = pack,
                        selected = pack.pathName == state.selectedPackPathName,
                        onClick = { vm.selectPack(pack.pathName) },
                    )
                }
            }
            item {
                Surface(
                    modifier = Modifier
                        .fillMaxWidth()
                        .heightIn(min = 360.dp),
                    shape = RoundedCornerShape(20.dp),
                    tonalElevation = 2.dp,
                ) {
                    if (selected == null) {
                        Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                            Text("Sync the repo, then pick a pack from the left to start editing.")
                        }
                    } else {
                        EditorPane(pack = selected, vm = vm)
                    }
                }
            }
            item {
            Surface(
                shape = RoundedCornerShape(20.dp),
                tonalElevation = 2.dp,
                modifier = Modifier
                    .fillMaxWidth(),
            ) {
                Column(
                    modifier = Modifier
                        .padding(12.dp),
                ) {
                    Text("Logs", style = MaterialTheme.typography.titleSmall)
                    Spacer(modifier = Modifier.height(8.dp))
                    state.logs.takeLast(20).forEach { log ->
                        Text(
                            text = log,
                            style = MaterialTheme.typography.bodySmall,
                            modifier = Modifier.padding(vertical = 2.dp),
                        )
                    }
                }
            }
            }
        }
    }
}

@Composable
private fun LoginGuideCard() {
    Surface(shape = RoundedCornerShape(20.dp), tonalElevation = 2.dp) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text("Login and publish guide", style = MaterialTheme.typography.titleMedium)
            Text(
                "This Android version follows the same high-level flow as the desktop publisher: sync repo, edit packs, rebuild index, then commit and push.",
                style = MaterialTheme.typography.bodyMedium,
            )
            StepLine("1. Create a GitHub Personal Access Token with repo contents write access.")
            StepLine("2. Paste owner/repo, branch, and token below.")
            StepLine("3. Tap Sync repo to pull packs and index.json into local cache.")
            StepLine("4. Edit a pack, then Save current or Save all, and Rebuild index.")
            StepLine("5. Tap Commit + Push to publish the changed files back to GitHub.")
        }
    }
}

@Composable
private fun StepLine(text: String) {
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.Top) {
        Box(
            modifier = Modifier
                .padding(top = 6.dp)
                .size(6.dp)
                .background(MaterialTheme.colorScheme.primary, RoundedCornerShape(999.dp))
        )
        Text(text, style = MaterialTheme.typography.bodySmall)
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ConfigCard(state: PublisherUiState, vm: PublisherViewModel) {
    Surface(shape = RoundedCornerShape(20.dp), tonalElevation = 2.dp) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("Repository config", style = MaterialTheme.typography.titleMedium)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterChip(
                    selected = state.loginConfirmed,
                    onClick = vm::confirmLogin,
                    label = { Text(if (state.loginConfirmed) "Login confirmed" else "Confirm login") },
                )
                FilterChip(
                    selected = false,
                    onClick = { vm.showTokenHelp(true) },
                    label = { Text("How to get token") },
                )
            }
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(
                    value = state.config.ownerRepo,
                    onValueChange = { vm.updateConfig { cfg -> cfg.copy(ownerRepo = it) } },
                    label = { Text("owner/repo") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                )
                OutlinedTextField(
                    value = state.config.branch,
                    onValueChange = { vm.updateConfig { cfg -> cfg.copy(branch = it) } },
                    label = { Text("Branch") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                )
            }
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(
                    value = state.config.token,
                    onValueChange = { vm.updateConfig { cfg -> cfg.copy(token = it) } },
                    label = { Text("GitHub token") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                    supportingText = {
                        Text("Classic PAT or fine-grained token with contents read/write access.")
                    },
                )
                OutlinedTextField(
                    value = state.config.commitMessage,
                    onValueChange = { vm.updateConfig { cfg -> cfg.copy(commitMessage = it) } },
                    label = { Text("Commit message") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                )
            }
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterChip(
                    selected = state.config.bumpChangedVersion,
                    onClick = {
                        vm.updateConfig { cfg -> cfg.copy(bumpChangedVersion = !cfg.bumpChangedVersion) }
                    },
                    label = { Text("Auto-bump changed packs") },
                )
                FilterChip(
                    selected = state.repoPath.isNotBlank(),
                    onClick = {},
                    enabled = false,
                    label = { Text(if (state.repoPath.isBlank()) "Local cache not synced" else state.repoPath) },
                )
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ActionRow(state: PublisherUiState, vm: PublisherViewModel) {
    FlowRow(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Button(onClick = vm::syncRepository, enabled = !state.busy && state.loginConfirmed) { Text("Sync repo") }
        Button(onClick = { vm.showCreatePackDialog(true) }, enabled = !state.busy && state.repoPath.isNotBlank()) {
            Text("New pack")
        }
        Button(onClick = vm::saveCurrentPack, enabled = !state.busy && state.selectedPackPathName != null) {
            Text("Save current")
        }
        Button(onClick = vm::saveAllPacks, enabled = !state.busy && state.packs.isNotEmpty()) { Text("Save all") }
        Button(onClick = vm::rebuildIndex, enabled = !state.busy && state.packs.isNotEmpty()) { Text("Rebuild index") }
        Button(
            onClick = vm::publish,
            enabled = !state.busy && state.loginConfirmed && state.packs.isNotEmpty(),
        ) {
            Text("Commit + Push")
        }

        if (state.busy) {
            Spacer(modifier = Modifier.width(8.dp))
            CircularProgressIndicator(modifier = Modifier.size(22.dp), strokeWidth = 2.dp)
        }
    }
}

@Composable
private fun PackListItem(
    pack: PackEditorState,
    selected: Boolean,
    onClick: () -> Unit,
) {
    val bg = if (selected) MaterialTheme.colorScheme.primaryContainer else Color.Transparent
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(bg)
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        Text(pack.name.ifBlank { pack.packId }, fontWeight = FontWeight.Medium)
        Text(pack.packId, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(pack.pathName, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun EditorPane(pack: PackEditorState, vm: PublisherViewModel) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Edit pack", style = MaterialTheme.typography.titleMedium)
        OutlinedTextField(
            value = pack.pathName,
            onValueChange = {},
            label = { Text("File") },
            enabled = false,
            modifier = Modifier.fillMaxWidth(),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            OutlinedTextField(
                value = pack.packId,
                onValueChange = { newValue ->
                    vm.updateSelectedPack { current -> current.copy(packId = newValue) }
                },
                label = { Text("ID") },
                modifier = Modifier.weight(1f),
            )
            OutlinedTextField(
                value = pack.language,
                onValueChange = { newValue ->
                    vm.updateSelectedPack { current -> current.copy(language = newValue) }
                },
                label = { Text("Language") },
                modifier = Modifier.width(140.dp),
            )
        }
        OutlinedTextField(
            value = pack.name,
            onValueChange = { newValue ->
                vm.updateSelectedPack { current -> current.copy(name = newValue) }
            },
            label = { Text("Name") },
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = pack.author,
            onValueChange = { newValue ->
                vm.updateSelectedPack { current -> current.copy(author = newValue) }
            },
            label = { Text("Author") },
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = pack.summary,
            onValueChange = { newValue ->
                vm.updateSelectedPack { current -> current.copy(summary = newValue) }
            },
            label = { Text("Summary") },
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = pack.tagsText,
            onValueChange = { newValue ->
                vm.updateSelectedPack { current -> current.copy(tagsText = newValue) }
            },
            label = { Text("Tags, comma separated") },
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = pack.serverTagsText,
            onValueChange = { newValue ->
                vm.updateSelectedPack { current -> current.copy(serverTagsText = newValue) }
            },
            label = { Text("Server tags, comma separated") },
            modifier = Modifier.fillMaxWidth(),
        )
        Text(
            text = "Version is auto-managed: ${pack.fileVersionLabel}",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(
            text = "updatedAt is auto-filled on rebuild/publish: ${pack.fileUpdatedAt.ifBlank { "pending" }}",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        OutlinedTextField(
            value = pack.entriesText,
            onValueChange = { newValue ->
                vm.updateSelectedPack { current -> current.copy(entriesText = newValue) }
            },
            label = { Text("Entries, one line each") },
            modifier = Modifier
                .fillMaxWidth()
                .height(260.dp),
        )
    }
}
