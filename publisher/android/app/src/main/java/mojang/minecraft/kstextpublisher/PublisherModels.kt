package mojang.minecraft.kstextpublisher

import java.io.File
import kotlinx.serialization.Serializable

data class BuildStats(
    val totalPacks: Int,
    val changedPacks: Int,
    val newPacks: Int,
    val unchangedPacks: Int,
)

data class BuildResult(
    val indexData: IndexPayload,
    val stats: BuildStats,
    val warnings: List<String>,
)

data class PackDocument(
    val path: File,
    var schemaVersion: Int,
    var packId: String,
    var name: String,
    var author: String,
    var summary: String,
    var language: String,
    var tags: List<String>,
    var serverTags: List<String>,
    var entries: List<String>,
    var fileVersion: Int,
    var fileUpdatedAt: String,
)

@Serializable
data class PackPayload(
    val schemaVersion: Int = 1,
    val id: String = "",
    val name: String = "",
    val author: String = "",
    val version: Int = 1,
    val updatedAt: String = "",
    val description: String = "",
    val language: String = "zh-CN",
    val tags: List<String> = emptyList(),
    val serverTags: List<String> = emptyList(),
    val entries: List<String> = emptyList(),
)

@Serializable
data class IndexPack(
    val id: String,
    val name: String,
    val author: String,
    val summary: String,
    val language: String,
    val tags: List<String>,
    val serverTags: List<String>,
    val version: Int,
    val updatedAt: String,
    val entryCount: Int,
    val sha256: String,
    val downloadUrl: String,
)

@Serializable
data class IndexPayload(
    val schemaVersion: Int,
    val generatedAt: String,
    val packs: List<IndexPack>,
)

data class RemotePackFile(
    val name: String,
    val path: String,
    val downloadUrl: String,
)

data class PublisherConfig(
    val ownerRepo: String = "bedkillerspacex-boop/KsText",
    val branch: String = "master",
    val token: String = "",
    val commitMessage: String = "update KsText packs",
    val bumpChangedVersion: Boolean = true,
)

data class PackEditorState(
    val pathName: String,
    val packId: String,
    val name: String,
    val author: String,
    val summary: String,
    val language: String,
    val tagsText: String,
    val serverTagsText: String,
    val entriesText: String,
    val fileVersionLabel: String,
    val fileUpdatedAt: String,
)

data class PublisherUiState(
    val config: PublisherConfig = PublisherConfig(),
    val loginConfirmed: Boolean = false,
    val repoPath: String = "",
    val summary: String = "Repository not synced",
    val status: String = "Ready",
    val busy: Boolean = false,
    val packs: List<PackEditorState> = emptyList(),
    val selectedPackPathName: String? = null,
    val logs: List<String> = emptyList(),
    val showCreatePackDialog: Boolean = false,
    val showTokenHelpDialog: Boolean = false,
    val newPackFileStem: String = "",
    val newPackDisplayName: String = "",
)
