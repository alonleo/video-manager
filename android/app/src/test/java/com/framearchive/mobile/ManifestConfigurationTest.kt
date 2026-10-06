package com.framearchive.mobile

import java.io.File
import javax.xml.parsers.DocumentBuilderFactory
import org.junit.Assert.assertEquals
import org.junit.Test

class ManifestConfigurationTest {
    @Test
    fun mainActivityHandlesScreenRotationWithoutRecreation() {
        val manifest = sequenceOf(
            File("src/main/AndroidManifest.xml"),
            File("app/src/main/AndroidManifest.xml"),
        ).first(File::isFile)
        val document = DocumentBuilderFactory.newInstance().apply {
            isNamespaceAware = true
        }.newDocumentBuilder().parse(manifest)
        val activity = document.getElementsByTagName("activity")
            .let { nodes -> (0 until nodes.length).map(nodes::item) }
            .first { node ->
                node.attributes.getNamedItemNS(ANDROID_NAMESPACE, "name")?.nodeValue == ".MainActivity"
            }
        val handledChanges = activity.attributes
            .getNamedItemNS(ANDROID_NAMESPACE, "configChanges")
            ?.nodeValue
            ?.split('|')
            ?.toSet()
            .orEmpty()

        assertEquals(
            setOf("keyboardHidden", "orientation", "screenSize", "smallestScreenSize"),
            handledChanges,
        )
    }

    private companion object {
        const val ANDROID_NAMESPACE = "http://schemas.android.com/apk/res/android"
    }
}
