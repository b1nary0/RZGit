import unittest

from rzgitclient.ui.panels.file_tree import FolderNode, build_tree, file_label


def shape(node: FolderNode) -> list:
    """The tree as nested lists: folder rows as (name, [...]), file rows as names."""
    return [(f.name, shape(f)) for f in node.folders] + [f.name for f in node.files]


class FileTreeTests(unittest.TestCase):
    def tree(self, *paths: str) -> FolderNode:
        return build_tree((p, p.upper()) for p in paths)

    def test_files_sit_under_their_folders(self):
        root = self.tree("README.md", "docs/a.md", "docs/b.md", "src/x.py")
        self.assertEqual(shape(root), [("docs", ["a.md", "b.md"]), ("src", ["x.py"]),
                                       "README.md"])

    def test_folders_first_then_files_each_by_name(self):
        root = self.tree("b.txt", "Z/1", "a/1", "A.txt")
        self.assertEqual([f.name for f in root.folders], ["a", "Z"])
        self.assertEqual([f.name for f in root.files], ["A.txt", "b.txt"])

    def test_a_chain_of_single_folders_is_one_row(self):
        root = self.tree("src/rzgitclient/ui/panels/a.py", "src/rzgitclient/ui/b.py")
        self.assertEqual(shape(root), [("src/rzgitclient/ui", [("panels", ["a.py"]), "b.py"])])
        self.assertEqual(root.folders[0].path, "src/rzgitclient/ui")
        self.assertEqual(root.folders[0].folders[0].path, "src/rzgitclient/ui/panels")

    def test_a_folder_with_files_is_not_merged(self):
        root = self.tree("src/a.py", "src/pkg/b.py")
        self.assertEqual(shape(root), [("src", [("pkg", ["b.py"]), "a.py"])])

    def test_file_nodes_keep_path_and_data(self):
        node = self.tree("docs/a.md").folders[0].files[0]
        self.assertEqual((node.name, node.path, node.data), ("a.md", "docs/a.md", "DOCS/A.MD"))

    def test_all_files_in_tree_order(self):
        root = self.tree("z.txt", "b/2", "a/1", "b/c/3")
        self.assertEqual([f.path for f in root.all_files()], ["a/1", "b/c/3", "b/2", "z.txt"])
        self.assertEqual([f.path for f in root.folders[1].all_files()], ["b/c/3", "b/2"])

    def test_empty(self):
        self.assertEqual(shape(self.tree()), [])

    def test_file_label(self):
        self.assertEqual(file_label("src/a.py"), "a.py")
        self.assertEqual(file_label("src/new.py", "src/old.py"), "old.py → new.py")
        self.assertEqual(file_label("lib/a.py", "src/a.py"), "src/a.py → a.py")
        self.assertEqual(file_label("top.txt", "old.txt"), "old.txt → top.txt")


if __name__ == "__main__":
    unittest.main()
