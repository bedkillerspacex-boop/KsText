package mojang.minecraft.kstextpublisher

import android.util.Base64
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put

class GitHubPublisherService(
    private val cacheRoot: File,
) {
    private val json = Json { ignoreUnknownKeys = true; prettyPrint = true }

    fun syncRepo(
        ownerRepo: String,
        branch: String,
        token: String,
        log: (String) -> Unit,
    ): File {
        val normalizedRepo = PublisherLogic.normalizeOwnerRepo(ownerRepo)
        val repoDir = PublisherLogic.cacheDirForRepo(cacheRoot, normalizedRepo, branch)
        val stagingDir = File(repoDir.parentFile, "${repoDir.name}.syncing")
        if (stagingDir.exists()) {
            stagingDir.deleteRecursively()
        }
        val packsDir = File(stagingDir, "packs")
        packsDir.mkdirs()

        log("Sync $normalizedRepo@$branch")
        val remotePacks = fetchPackList(normalizedRepo, branch, token)
        require(remotePacks.isNotEmpty()) { "No pack files found in remote repository" }
        for (remote in remotePacks) {
            val content = getText(remote.downloadUrl, token.takeIf { it.isNotBlank() })
            File(stagingDir, remote.path).apply {
                parentFile?.mkdirs()
                writeText(content, Charsets.UTF_8)
            }
            log("Downloaded ${remote.name}")
        }

        val indexUrl = "https://raw.githubusercontent.com/$normalizedRepo/$branch/index.json"
        runCatching {
            val indexText = getText(indexUrl, token.takeIf { it.isNotBlank() })
            File(stagingDir, "index.json").writeText(indexText, Charsets.UTF_8)
            log("Downloaded index.json")
        }

        if (repoDir.exists()) {
            repoDir.deleteRecursively()
        }
        stagingDir.copyRecursively(repoDir, overwrite = true)
        stagingDir.deleteRecursively()

        return repoDir
    }

    fun publishRepo(
        repoDir: File,
        ownerRepo: String,
        branch: String,
        token: String,
        commitMessage: String,
        log: (String) -> Unit,
    ) {
        require(token.isNotBlank()) { "GitHub token is required for publish" }
        val normalizedRepo = PublisherLogic.normalizeOwnerRepo(ownerRepo)
        val files = mutableListOf<File>()
        files += PublisherLogic.collectPackFiles(repoDir)
        val indexFile = File(repoDir, "index.json")
        if (indexFile.exists()) {
            files += indexFile
        }

        var changedCount = 0
        for (file in files) {
            val relativePath = repoDir.toPath().relativize(file.toPath()).toString().replace(File.separatorChar, '/')
            val remoteText = fetchRemoteFileTextOrNull(normalizedRepo, branch, relativePath, token)
            val localText = file.readText(Charsets.UTF_8)
            if (remoteText == localText) {
                log("Skip unchanged $relativePath")
                continue
            }
            val remoteSha = fetchRemoteFileShaOrNull(normalizedRepo, branch, relativePath, token)
            uploadFile(normalizedRepo, branch, relativePath, localText, remoteSha, token, commitMessage)
            changedCount++
            log("Published $relativePath")
        }

        if (changedCount == 0) {
            log("No remote changes to publish")
        } else {
            log("Publish finished, updated $changedCount files")
        }
    }

    private fun fetchPackList(ownerRepo: String, branch: String, token: String): List<RemotePackFile> {
        val branchUrl = "https://api.github.com/repos/$ownerRepo/branches/$branch"
        val branchResponse = request("GET", branchUrl, token.takeIf { it.isNotBlank() }, null)
        val treeSha = json.parseToJsonElement(branchResponse).jsonObject["commit"]
            ?.jsonObject?.get("commit")
            ?.jsonObject?.get("tree")
            ?.jsonObject?.get("sha")
            ?.jsonPrimitive?.contentOrNull
            ?: error("Unable to resolve branch tree sha")
        val url = "https://api.github.com/repos/$ownerRepo/git/trees/$treeSha?recursive=1"
        val response = request("GET", url, token.takeIf { it.isNotBlank() }, null)
        val array = json.parseToJsonElement(response).jsonObject["tree"]?.jsonArray ?: JsonArray(emptyList())
        return array.mapNotNull { element ->
            val item = element.jsonObject
            val type = item["type"]?.jsonPrimitive?.contentOrNull
            if (type != "file") return@mapNotNull null
            val path = item["path"]?.jsonPrimitive?.contentOrNull ?: return@mapNotNull null
            if (!path.startsWith("packs/") || !path.endsWith(".json", ignoreCase = true)) return@mapNotNull null
            RemotePackFile(
                name = path.substringAfterLast('/'),
                path = path,
                downloadUrl = "https://raw.githubusercontent.com/$ownerRepo/$branch/$path",
            )
        }.sortedBy { it.path.lowercase() }
    }

    private fun fetchRemoteFileShaOrNull(ownerRepo: String, branch: String, path: String, token: String): String? {
        val encodedPath = path.split("/").joinToString("/") { encodePathSegment(it) }
        val url = "https://api.github.com/repos/$ownerRepo/contents/$encodedPath?ref=$branch"
        return runCatching {
            val response = request("GET", url, token, null)
            json.parseToJsonElement(response).jsonObject["sha"]?.jsonPrimitive?.content
        }.getOrNull()
    }

    private fun fetchRemoteFileTextOrNull(ownerRepo: String, branch: String, path: String, token: String): String? {
        val encodedPath = path.split("/").joinToString("/") { encodePathSegment(it) }
        val url = "https://raw.githubusercontent.com/$ownerRepo/$branch/$encodedPath"
        return runCatching { getText(url, token.takeIf { it.isNotBlank() }) }.getOrNull()
    }

    private fun uploadFile(
        ownerRepo: String,
        branch: String,
        path: String,
        text: String,
        sha: String?,
        token: String,
        commitMessage: String,
    ) {
        val encodedPath = path.split("/").joinToString("/") { encodePathSegment(it) }
        val url = "https://api.github.com/repos/$ownerRepo/contents/$encodedPath"
        val payload = buildJsonObject {
            put("message", JsonPrimitive(commitMessage.ifBlank { "update KsText packs" }))
            put("branch", JsonPrimitive(branch))
            put("content", JsonPrimitive(Base64.encodeToString(text.toByteArray(Charsets.UTF_8), Base64.NO_WRAP)))
            if (!sha.isNullOrBlank()) {
                put("sha", JsonPrimitive(sha))
            }
        }
        request("PUT", url, token, json.encodeToString(JsonObject.serializer(), payload))
    }

    private fun getText(urlText: String, token: String?): String {
        val connection = (URL(urlText).openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"
            connectTimeout = 15000
            readTimeout = 20000
            setRequestProperty("Accept", "application/vnd.github+json")
            if (!token.isNullOrBlank()) {
                setRequestProperty("Authorization", "Bearer $token")
            }
        }
        val code = connection.responseCode
        val stream = if (code in 200..299) connection.inputStream else connection.errorStream
        val text = BufferedReader(InputStreamReader(stream, Charsets.UTF_8)).use { it.readText() }
        if (code !in 200..299) {
            throw IllegalStateException("Request failed ($code): $text")
        }
        return text
    }

    private fun request(method: String, urlText: String, token: String?, body: String?): String {
        val connection = (URL(urlText).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 15000
            readTimeout = 20000
            doInput = true
            setRequestProperty("Accept", "application/vnd.github+json")
            setRequestProperty("X-GitHub-Api-Version", "2022-11-28")
            if (!token.isNullOrBlank()) {
                setRequestProperty("Authorization", "Bearer $token")
            }
            if (body != null) {
                doOutput = true
                setRequestProperty("Content-Type", "application/json; charset=utf-8")
            }
        }
        if (body != null) {
            connection.outputStream.use { output ->
                output.write(body.toByteArray(Charsets.UTF_8))
            }
        }
        val code = connection.responseCode
        val stream = if (code in 200..299) connection.inputStream else connection.errorStream
        val text = BufferedReader(InputStreamReader(stream, Charsets.UTF_8)).use { it.readText() }
        if (code !in 200..299) {
            throw IllegalStateException("GitHub API failed ($code): $text")
        }
        return text
    }

    private fun encodePathSegment(segment: String): String =
        URLEncoder.encode(segment, "UTF-8").replace("+", "%20")
}
