package mojang.minecraft.kstextpublisher

import java.io.File
import java.net.URLEncoder
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.Instant
import kotlinx.serialization.json.Json

object PublisherLogic {
    private val json = Json {
        prettyPrint = true
        prettyPrintIndent = "  "
        ignoreUnknownKeys = true
        encodeDefaults = true
    }

    private val defaultTags = listOf("killsay", "community")
    private val defaultServerTags = listOf("generic")

    fun utcNow(): String = Instant.now().toString()

    fun normalizeOwnerRepo(value: String): String {
        var text = value.trim()
        if (text.isEmpty()) return "bedkillerspacex-boop/KsText"
        if (text.startsWith("https://github.com/")) {
            text = text.removePrefix("https://github.com/")
        } else if (text.startsWith("http://github.com/")) {
            text = text.removePrefix("http://github.com/")
        }
        if (text.endsWith(".git")) {
            text = text.removeSuffix(".git")
        }
        return text.trim('/').ifEmpty { "bedkillerspacex-boop/KsText" }
    }

    fun cacheDirForRepo(rootDir: File, ownerRepo: String, branch: String): File {
        val safeRepo = normalizeOwnerRepo(ownerRepo)
            .replace("/", "__")
            .replace("\\", "__")
            .replace(":", "_")
        val safeBranch = branch.trim().ifEmpty { "master" }
            .replace("/", "__")
            .replace("\\", "__")
            .replace(":", "_")
        return File(rootDir, "$safeRepo/$safeBranch")
    }

    fun hasCachedRepo(rootDir: File, ownerRepo: String, branch: String): Boolean {
        val repoDir = cacheDirForRepo(rootDir, ownerRepo, branch)
        return File(repoDir, "packs").isDirectory
    }

    fun buildSummary(result: BuildResult): String {
        val stats = result.stats
        return "Total ${stats.totalPacks} packs | New ${stats.newPacks} | Changed ${stats.changedPacks} | Unchanged ${stats.unchangedPacks}"
    }

    fun loadExistingIndex(indexPath: File): Map<String, IndexPack> {
        if (!indexPath.exists()) return emptyMap()
        return runCatching {
            json.decodeFromString<IndexPayload>(indexPath.readText(Charsets.UTF_8))
        }.getOrNull()?.packs?.associateBy { it.id } ?: emptyMap()
    }

    fun collectPackFiles(repoDir: File): List<File> {
        val packsDir = File(repoDir, "packs")
        require(packsDir.isDirectory) { "Missing packs directory: ${packsDir.absolutePath}" }
        return packsDir.listFiles { file -> file.isFile && file.extension.equals("json", ignoreCase = true) }
            ?.sortedBy { it.name.lowercase() }
            ?: emptyList()
    }

    fun validatePackFileStem(fileStem: String): String {
        val text = fileStem.trim()
        require(text.isNotEmpty()) { "File name cannot be empty" }
        require('/' !in text && '\\' !in text) { "File name cannot contain path separators" }
        require(text != "." && text != "..") { "File name is invalid" }
        val fileName = if (text.endsWith(".json", ignoreCase = true)) text else "$text.json"
        require(File(fileName).name == fileName) { "File name is invalid" }
        return text
    }

    fun newPackDocument(repoDir: File, fileStem: String, displayName: String): PackDocument {
        val validated = validatePackFileStem(fileStem)
        val fileName = if (validated.endsWith(".json", ignoreCase = true)) validated else "$validated.json"
        val path = File(File(repoDir, "packs"), fileName)
        require(!path.exists()) { "File already exists: ${path.name}" }
        return PackDocument(
            path = path,
            schemaVersion = 1,
            packId = validated.removeSuffix(".json"),
            name = displayName.ifBlank { validated },
            author = "Anonymous",
            summary = displayName.ifBlank { validated },
            language = "zh-CN",
            tags = defaultTags,
            serverTags = defaultServerTags,
            entries = listOf("{name}"),
            fileVersion = 1,
            fileUpdatedAt = utcNow(),
        )
    }

    fun loadPackDocuments(repoDir: File): Triple<List<PackDocument>, Map<String, IndexPack>, List<String>> {
        val existingById = loadExistingIndex(File(repoDir, "index.json"))
        val warnings = mutableListOf<String>()
        val documents = mutableListOf<PackDocument>()
        for (packFile in collectPackFiles(repoDir)) {
            runCatching {
                val payload = json.decodeFromString<PackPayload>(packFile.readText(Charsets.UTF_8))
                val fileId = payload.id.trim().ifEmpty { packFile.nameWithoutExtension }
                val existing = existingById[fileId]
                val author = payload.author.trim().ifEmpty {
                    existing?.author?.trim().orEmpty().ifEmpty { "Anonymous" }
                }
                if (payload.author.trim().isEmpty() && existing?.author?.trim().isNullOrEmpty()) {
                    warnings += "${packFile.name}: author was empty, filled with Anonymous"
                }
                PackDocument(
                    path = packFile,
                    schemaVersion = payload.schemaVersion,
                    packId = fileId,
                    name = payload.name.trim().ifEmpty { existing?.name ?: packFile.nameWithoutExtension },
                    author = author,
                    summary = payload.description.trim().ifEmpty { existing?.summary ?: packFile.nameWithoutExtension },
                    language = payload.language.trim().ifEmpty { existing?.language ?: "zh-CN" },
                    tags = payload.tags.map { it.trim() }.filter { it.isNotBlank() }.ifEmpty { existing?.tags ?: defaultTags },
                    serverTags = payload.serverTags.map { it.trim() }.filter { it.isNotBlank() }.ifEmpty { existing?.serverTags ?: defaultServerTags },
                    entries = payload.entries.map { it.trim() }.filter { it.isNotBlank() }.ifEmpty {
                        throw IllegalArgumentException("${packFile.name}: entries cannot be empty")
                    },
                    fileVersion = payload.version.takeIf { it > 0 } ?: existing?.version ?: 1,
                    fileUpdatedAt = payload.updatedAt.ifBlank { existing?.updatedAt.orEmpty() },
                )
            }.onSuccess { documents += it }
                .onFailure { error ->
                    warnings += "${packFile.name}: skipped because ${error.message ?: error}"
                }
        }
        return Triple(documents, existingById, warnings)
    }

    fun normalizePackDocument(doc: PackDocument) {
        doc.packId = doc.packId.trim().ifEmpty { doc.path.nameWithoutExtension }
        doc.name = doc.name.trim().ifEmpty { doc.path.nameWithoutExtension }
        doc.author = doc.author.trim().ifEmpty { "Anonymous" }
        doc.summary = doc.summary.trim().ifEmpty { doc.name }
        doc.language = doc.language.trim().ifEmpty { "zh-CN" }
        doc.tags = doc.tags.map { it.trim() }.filter { it.isNotBlank() }.ifEmpty { defaultTags }
        doc.serverTags = doc.serverTags.map { it.trim() }.filter { it.isNotBlank() }.ifEmpty { defaultServerTags }
        doc.entries = doc.entries.map { it.trim() }.filter { it.isNotBlank() }
        if (doc.fileVersion <= 0) {
            doc.fileVersion = 1
        }
        require(doc.entries.isNotEmpty()) { "${doc.path.name}: entries cannot be empty" }
    }

    fun packPayload(doc: PackDocument): PackPayload {
        normalizePackDocument(doc)
        return PackPayload(
            schemaVersion = maxOf(doc.schemaVersion, 1),
            id = doc.packId,
            name = doc.name,
            author = doc.author,
            version = maxOf(doc.fileVersion, 1),
            updatedAt = doc.fileUpdatedAt.ifBlank { utcNow() },
            description = doc.summary,
            language = doc.language,
            tags = doc.tags,
            serverTags = doc.serverTags,
            entries = doc.entries,
        )
    }

    fun payloadBytes(payload: PackPayload): ByteArray =
        (json.encodeToString(PackPayload.serializer(), payload) + "\n").toByteArray(Charsets.UTF_8)

    fun sha256Bytes(bytes: ByteArray): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(bytes)
        return digest.joinToString("") { byte -> "%02x".format(byte) }
    }

    fun savePackDocument(doc: PackDocument) {
        val payload = packPayload(doc)
        doc.fileVersion = payload.version
        doc.fileUpdatedAt = payload.updatedAt
        doc.path.parentFile?.mkdirs()
        doc.path.writeBytes(payloadBytes(payload))
    }

    fun saveAllPackDocuments(documents: List<PackDocument>) {
        documents.forEach(::savePackDocument)
    }

    fun rawDownloadUrl(ownerRepo: String, branch: String, fileName: String): String {
        val encoded = URLEncoder.encode(fileName, StandardCharsets.UTF_8.name()).replace("+", "%20")
        val normalizedRepo = normalizeOwnerRepo(ownerRepo)
        val safeBranch = branch.ifBlank { "master" }
        return "https://raw.githubusercontent.com/$normalizedRepo/$safeBranch/packs/$encoded"
    }

    fun buildIndexFromDocuments(
        ownerRepo: String,
        branch: String,
        documents: List<PackDocument>,
        existingById: Map<String, IndexPack>,
        baseWarnings: List<String>,
        bumpChangedVersion: Boolean,
    ): BuildResult {
        val generatedAt = utcNow()
        val seenIds = linkedSetOf<String>()
        val packs = mutableListOf<IndexPack>()
        var changed = 0
        var added = 0
        var unchanged = 0

        for (doc in documents) {
            normalizePackDocument(doc)
            require(seenIds.add(doc.packId)) { "Duplicate pack id: ${doc.packId}" }

            val existing = existingById[doc.packId]
            val initialPayload = packPayload(doc)
            val initialSha = sha256Bytes(payloadBytes(initialPayload))
            val oldSha = existing?.sha256.orEmpty()
            val isNew = existing == null
            val isChanged = isNew || initialSha != oldSha
            val baseVersion = maxOf(doc.fileVersion, existing?.version ?: 1, 1)

            val version = when {
                isNew -> baseVersion
                isChanged && bumpChangedVersion -> baseVersion + 1
                else -> baseVersion
            }

            val updatedAt = if (isChanged) {
                generatedAt
            } else {
                doc.fileUpdatedAt.ifBlank { existing?.updatedAt.orEmpty().ifBlank { generatedAt } }
            }

            doc.fileVersion = version
            doc.fileUpdatedAt = updatedAt
            val finalPayload = packPayload(doc)
            val finalSha = sha256Bytes(payloadBytes(finalPayload))

            packs += IndexPack(
                id = doc.packId,
                name = doc.name,
                author = doc.author,
                summary = doc.summary,
                language = doc.language,
                tags = doc.tags,
                serverTags = doc.serverTags,
                version = version,
                updatedAt = updatedAt,
                entryCount = doc.entries.size,
                sha256 = finalSha,
                downloadUrl = rawDownloadUrl(ownerRepo, branch, doc.path.name),
            )

            when {
                isNew -> added++
                isChanged -> changed++
                else -> unchanged++
            }
        }

        return BuildResult(
            indexData = IndexPayload(
                schemaVersion = 1,
                generatedAt = generatedAt,
                packs = packs.sortedBy { it.name.lowercase() },
            ),
            stats = BuildStats(
                totalPacks = packs.size,
                changedPacks = changed,
                newPacks = added,
                unchangedPacks = unchanged,
            ),
            warnings = baseWarnings,
        )
    }

    fun writeIndexFile(repoDir: File, result: BuildResult): File {
        val indexPath = File(repoDir, "index.json")
        indexPath.writeText(json.encodeToString(IndexPayload.serializer(), result.indexData) + "\n", Charsets.UTF_8)
        return indexPath
    }

    fun editorStateOf(doc: PackDocument): PackEditorState = PackEditorState(
        pathName = doc.path.name,
        packId = doc.packId,
        name = doc.name,
        author = doc.author,
        summary = doc.summary,
        language = doc.language,
        tagsText = doc.tags.joinToString(", "),
        serverTagsText = doc.serverTags.joinToString(", "),
        entriesText = doc.entries.joinToString("\n"),
        fileVersionLabel = doc.fileVersion.toString(),
        fileUpdatedAt = doc.fileUpdatedAt,
    )

    fun applyEditorState(doc: PackDocument, state: PackEditorState) {
        doc.packId = state.packId
        doc.name = state.name
        doc.author = state.author
        doc.summary = state.summary
        doc.language = state.language
        doc.tags = state.tagsText.split(",").map { it.trim() }.filter { it.isNotBlank() }
        doc.serverTags = state.serverTagsText.split(",").map { it.trim() }.filter { it.isNotBlank() }
        doc.entries = state.entriesText.lines().map { it.trim() }.filter { it.isNotBlank() }
    }
}
