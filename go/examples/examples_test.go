package examples_test

import (
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"testing"
)

// Every example is a main package whose work is in run(ctx, ...), so its test
// can drive it, and which does nothing before main does.
func TestEveryExampleIsDrivableAndDoesNothingOnLoad(t *testing.T) {
	entries, err := os.ReadDir(".")
	if err != nil {
		t.Fatal(err)
	}
	examples := 0
	for _, entry := range entries {
		if !entry.IsDir() || entry.Name() == "internal" {
			continue
		}
		examples++
		dir := entry.Name()
		if _, err := os.Stat(filepath.Join(dir, "main_test.go")); err != nil {
			t.Errorf("%s has no main_test.go", dir)
		}
		file, err := parser.ParseFile(token.NewFileSet(), filepath.Join(dir, "main.go"), nil, parser.ParseComments)
		if err != nil {
			t.Errorf("%s: %v", dir, err)
			continue
		}
		if file.Name.Name != "main" || file.Doc == nil {
			t.Errorf("%s/main.go is not a documented main package", dir)
		}
		declared := map[string]bool{}
		for _, decl := range file.Decls {
			if fn, ok := decl.(*ast.FuncDecl); ok && fn.Recv == nil {
				if fn.Name.Name == "init" {
					t.Errorf("%s/main.go declares init", dir)
				}
				declared[fn.Name.Name] = true
			}
		}
		if !declared["main"] || !declared["run"] {
			t.Errorf("%s/main.go declares %v, not main and run", dir, declared)
		}
	}
	if examples != 10 {
		t.Fatalf("found %d examples", examples)
	}
}
