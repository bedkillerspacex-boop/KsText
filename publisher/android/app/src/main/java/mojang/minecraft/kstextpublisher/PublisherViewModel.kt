package mojang.minecraft.kstextpublisher

import android.app.Application
import android.content.Context
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class PublisherViewModel(application: Application) : AndroidViewModel(application) {
    private val cacheRoot = File(application.filesDir, "kstext_publisher_cache")
    private val service = GitHubPublisherService(cacheRoot)
    private val prefs = application.getSharedPreferences("kstext_publisher", Context.MODE_PRIVATE)
    private val _uiState = MutableStateFlow(PublisherUiState())
    val uiState: StateFlow<PublisherUiState> = _uiState.asStateFlow()

    private var repoDir: File? = null
    private var documents: MutableList<PackDocument> = mutableListOf()
    private var existingIndexById: Map<String, IndexPack> = emptyMap()
    private var lastWarnings: List<String> = emptyList()

    init {
        restoreSession()
    }

    fun updateConfig(transform: (PublisherConfig) -> PublisherConfig) {
        _uiState.update { it.copy(config = transform(it.config), loginConfirmed = false) }
        persistConfig(_uiState.value.config)
    }

    fun confirmLogin() {
        val config = _uiState.value.config
        val normalized = PublisherLogic.normalizeOwnerRepo(config.ownerRepo)
        val tokenStatus = if (config.token.isBlank()) "read-only sync" else "token ready"
        _uiState.update {
            it.copy(
                config = config.copy(ownerRepo = normalized, branch = config.branch.ifBlank { "master" }),
                loginConfirmed = true,
                status = "Login confirmed: $normalized@${config.branch.ifBlank { "master" }} ($tokenStatus)",
            )
        }
        persistConfig(_uiState.value.config)
        appendLog("Login confirmed for $normalized@${config.branch.ifBlank { "master" }}")
        loadCachedRepoIfPresent()
    }

    fun showTokenHelp(show: Boolean) {
        _uiState.update { it.copy(showTokenHelpDialog = show) }
    }

    fun selectPack(pathName: String) {
        _uiState.update { it.copy(selectedPackPathName = pathName) }
    }

    fun updateSelectedPack(transform: (PackEditorState) -> PackEditorState) {
        val selectedPath = _uiState.value.selectedPackPathName ?: return
        _uiState.update { state ->
            state.copy(
                packs = state.packs.map { pack ->
                    if (pack.pathName == selectedPath) transform(pack) else pack
                }
            )
        }
    }

    fun showCreatePackDialog(show: Boolean) {
        _uiState.update { it.copy(showCreatePackDialog = show) }
    }

    fun updateCreatePackFields(fileStem: String? = null, displayName: String? = null) {
        _uiState.update {
            it.copy(
                newPackFileStem = fileStem ?: it.newPackFileStem,
                newPackDisplayName = displayName ?: it.newPackDisplayName,
            )
        }
    }

    fun syncRepository() = launchBusy("Syncing repository...") {
        require(_uiState.value.loginConfirmed) { "Confirm login first" }
        syncRepositoryInternal(_uiState.value.config)
    }

    fun saveCurrentPack() = launchBusy("Saving current pack...") {
        applyUiEditsToDocuments()
        val selectedPath = _uiState.value.selectedPackPathName ?: return@launchBusy
        documents.firstOrNull { it.path.name == selectedPath }?.let(PublisherLogic::savePackDocument)
        appendLog("Saved $selectedPath")
        reloadDocuments(repoDir)
    }

    fun saveAllPacks() = launchBusy("Saving all packs...") {
        applyUiEditsToDocuments()
        PublisherLogic.saveAllPackDocuments(documents)
        appendLog("All packs saved")
        reloadDocuments(repoDir)
    }

    fun rebuildIndex() = launchBusy("Rebuilding index.json...") {
        applyUiEditsToDocuments()
        val repo = requireNotNull(repoDir) { "Sync the repository first" }
        val result = PublisherLogic.buildIndexFromDocuments(
            ownerRepo = _uiState.value.config.ownerRepo,
            branch = _uiState.value.config.branch,
            documents = documents,
            existingById = existingIndexById,
            baseWarnings = lastWarnings,
            bumpChangedVersion = _uiState.value.config.bumpChangedVersion,
        )
        PublisherLogic.saveAllPackDocuments(documents)
        PublisherLogic.writeIndexFile(repo, result)
        appendLog(PublisherLogic.buildSummary(result))
        result.warnings.forEach(::appendLog)
        reloadDocuments(repo)
        _uiState.update {
            it.copy(
                summary = PublisherLogic.buildSummary(result),
                status = "index.json rebuilt",
            )
        }
    }

    private suspend fun syncRepositoryInternal(config: PublisherConfig) {
        val repo = service.syncRepo(config.ownerRepo, config.branch, config.token, ::appendLog)
        repoDir = repo
        persistConfig(config)
        reloadDocuments(repo)
        _uiState.update {
            it.copy(
                repoPath = repo.absolutePath,
                status = "Repository synced",
            )
        }
    }

    fun publish() = launchBusy("Commit + push in progress...") {
        require(_uiState.value.loginConfirmed) { "Confirm login first" }
        val repo = requireNotNull(repoDir) { "Sync the repository first" }
        applyUiEditsToDocuments()
        val config = _uiState.value.config
        val result = PublisherLogic.buildIndexFromDocuments(
            ownerRepo = config.ownerRepo,
            branch = config.branch,
            documents = documents,
            existingById = existingIndexById,
            baseWarnings = lastWarnings,
            bumpChangedVersion = config.bumpChangedVersion,
        )
        PublisherLogic.saveAllPackDocuments(documents)
        PublisherLogic.writeIndexFile(repo, result)
        service.publishRepo(
            repoDir = repo,
            ownerRepo = config.ownerRepo,
            branch = config.branch,
            token = config.token,
            commitMessage = config.commitMessage,
            log = ::appendLog,
        )
        reloadDocuments(repo)
        _uiState.update {
            it.copy(
                summary = PublisherLogic.buildSummary(result),
                status = "Commit + push completed",
            )
        }
    }

    fun createPack() = launchBusy("Creating pack...") {
        val repo = requireNotNull(repoDir) { "Sync the repository first" }
        val state = _uiState.value
        val document = PublisherLogic.newPackDocument(repo, state.newPackFileStem, state.newPackDisplayName)
        documents += document
        PublisherLogic.savePackDocument(document)
        appendLog("Created ${document.path.name}")
        reloadDocuments(repo, selectPackPathName = document.path.name)
        _uiState.update {
            it.copy(
                showCreatePackDialog = false,
                newPackFileStem = "",
                newPackDisplayName = "",
            )
        }
    }

    private suspend fun reloadDocuments(repo: File?, selectPackPathName: String? = null) {
        val actualRepo = repo ?: return
        val (loadedDocuments, existing, warnings) = withContext(Dispatchers.IO) {
            PublisherLogic.loadPackDocuments(actualRepo)
        }
        documents = loadedDocuments.toMutableList()
        existingIndexById = existing
        lastWarnings = warnings
        val packs = documents.map(PublisherLogic::editorStateOf)
        val selected = selectPackPathName ?: _uiState.value.selectedPackPathName ?: packs.firstOrNull()?.pathName
        _uiState.update {
            it.copy(
                packs = packs,
                selectedPackPathName = selected,
                summary = if (packs.isEmpty()) "No packs found" else "Loaded ${packs.size} packs",
            )
        }
    }

    private fun restoreSession() {
        val config = PublisherConfig(
            ownerRepo = prefs.getString("ownerRepo", "bedkillerspacex-boop/KsText") ?: "bedkillerspacex-boop/KsText",
            branch = prefs.getString("branch", "master") ?: "master",
            token = prefs.getString("token", "") ?: "",
            commitMessage = prefs.getString("commitMessage", "update KsText packs") ?: "update KsText packs",
            bumpChangedVersion = prefs.getBoolean("bumpChangedVersion", true),
        )
        _uiState.update {
            it.copy(
                config = config,
                loginConfirmed = prefs.getBoolean("loginConfirmed", false),
            )
        }
        loadCachedRepoIfPresent()
    }

    private fun loadCachedRepoIfPresent() {
        val config = _uiState.value.config
        if (!PublisherLogic.hasCachedRepo(cacheRoot, config.ownerRepo, config.branch)) {
            syncIfLoginConfirmed("No usable cache found, syncing from GitHub...")
            return
        }
        val cached = PublisherLogic.cacheDirForRepo(cacheRoot, config.ownerRepo, config.branch)
        repoDir = cached
        viewModelScope.launch {
            runCatching { reloadDocuments(cached) }
                .onSuccess {
                    appendLog("Loaded cached repo ${cached.absolutePath}")
                    _uiState.update { state -> state.copy(repoPath = cached.absolutePath) }
                }
                .onFailure { error ->
                    appendLog("Cache load failed: ${error.message ?: error}")
                    syncIfLoginConfirmed("Cache is stale or broken, syncing from GitHub...")
                }
        }
    }

    private fun syncIfLoginConfirmed(status: String) {
        if (!_uiState.value.loginConfirmed) return
        launchBusy(status) {
            syncRepositoryInternal(_uiState.value.config)
        }
    }

    private fun persistConfig(config: PublisherConfig) {
        prefs.edit()
            .putString("ownerRepo", config.ownerRepo)
            .putString("branch", config.branch)
            .putString("token", config.token)
            .putString("commitMessage", config.commitMessage)
            .putBoolean("bumpChangedVersion", config.bumpChangedVersion)
            .putBoolean("loginConfirmed", _uiState.value.loginConfirmed)
            .apply()
    }

    private fun applyUiEditsToDocuments() {
        val stateByPath = _uiState.value.packs.associateBy { it.pathName }
        documents.forEach { doc ->
            stateByPath[doc.path.name]?.let { ui ->
                PublisherLogic.applyEditorState(doc, ui)
            }
        }
    }

    private fun appendLog(message: String) {
        _uiState.update { state ->
            state.copy(
                logs = (state.logs + message).takeLast(120),
                status = message,
            )
        }
    }

    private fun launchBusy(status: String, block: suspend () -> Unit) {
        viewModelScope.launch {
            _uiState.update { it.copy(busy = true, status = status) }
            runCatching { withContext(Dispatchers.IO) { block() } }
                .onFailure { error -> appendLog(error.message ?: error.toString()) }
            _uiState.update { it.copy(busy = false) }
        }
    }
}
