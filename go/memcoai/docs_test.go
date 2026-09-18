package memcoai

import (
	"go/ast"
	"go/doc"
	"go/parser"
	"go/token"
	"os"
	"slices"
	"strings"
	"testing"
)

func documented(t *testing.T) (*doc.Package, map[string]bool) {
	t.Helper()
	files := token.NewFileSet()
	var sources, tests []*ast.File
	entries, err := os.ReadDir(".")
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range entries {
		name := entry.Name()
		if !strings.HasSuffix(name, ".go") {
			continue
		}
		file, err := parser.ParseFile(files, name, nil, parser.ParseComments)
		if err != nil {
			t.Fatal(err)
		}
		if strings.HasSuffix(name, "_test.go") {
			tests = append(tests, file)
		} else {
			sources = append(sources, file)
		}
	}
	pkg, err := doc.NewFromFiles(files, append(sources, tests...), "github.com/memcoai/memcoai/go/memcoai")
	if err != nil {
		t.Fatal(err)
	}
	examples := map[string]bool{}
	for _, example := range doc.Examples(tests...) {
		examples[example.Name] = true
	}
	return pkg, examples
}

// exempt are methods whose meaning is fixed by an interface they satisfy.
var exempt = []string{"Error", "Unwrap", "String", "Format", "LogValue"}

func checkFunc(t *testing.T, name string, fn *doc.Func, examples map[string]bool, needExample bool) {
	t.Helper()
	if strings.TrimSpace(fn.Doc) == "" {
		t.Errorf("%s has no doc comment", name)
		return
	}
	if slices.Contains(exempt, fn.Name) {
		return
	}
	params := fn.Decl.Type.Params.List
	if len(params) > 0 && !strings.Contains(fn.Doc, "Parameters:") {
		t.Errorf("%s does not describe its parameters", name)
	}
	for _, field := range params {
		for _, param := range field.Names {
			if !strings.Contains(fn.Doc, "  - "+param.Name+":") {
				t.Errorf("%s does not describe %s", name, param.Name)
			}
		}
	}
	if results := fn.Decl.Type.Results; results != nil {
		for _, field := range results.List {
			if ident, ok := field.Type.(*ast.Ident); ok && ident.Name == "error" && !strings.Contains(fn.Doc, "Errors:") {
				t.Errorf("%s does not say which errors it returns", name)
			}
		}
	}
	if needExample && !examples[strings.ReplaceAll(name, ".", "_")] {
		t.Errorf("%s has no example", name)
	}
}

func TestEveryExportedSymbolIsDocumented(t *testing.T) {
	pkg, examples := documented(t)
	if strings.TrimSpace(pkg.Doc) == "" {
		t.Error("the package has no doc comment")
	}
	withExamples := []string{"NewClient", "ReadProvenance", "SetLevel", "Render", "Briefing"}
	exampleTypes := []string{"Client", "MemoryOperations", "Session", "Toolset", "Tool"}
	for _, fn := range pkg.Funcs {
		checkFunc(t, fn.Name, fn, examples, slices.Contains(withExamples, fn.Name))
	}
	for _, typ := range pkg.Types {
		if strings.TrimSpace(typ.Doc) == "" {
			t.Errorf("type %s has no doc comment", typ.Name)
		}
		for _, fn := range typ.Funcs {
			checkFunc(t, fn.Name, fn, examples, slices.Contains(withExamples, fn.Name))
		}
		for _, method := range typ.Methods {
			name := typ.Name + "." + method.Name
			needExample := slices.Contains(exampleTypes, typ.Name) || name == "Memory.Feedback"
			checkFunc(t, name, method, examples, needExample)
		}
		for _, spec := range typ.Decl.Specs {
			structure, ok := spec.(*ast.TypeSpec).Type.(*ast.StructType)
			if !ok {
				continue
			}
			for _, field := range structure.Fields.List {
				for _, name := range field.Names {
					if name.IsExported() && field.Doc == nil && field.Comment == nil {
						t.Errorf("%s.%s has no doc comment", typ.Name, name.Name)
					}
				}
			}
		}
	}
	for _, values := range [][]*doc.Value{pkg.Consts, pkg.Vars} {
		for _, value := range values {
			for _, spec := range value.Decl.Specs {
				s := spec.(*ast.ValueSpec)
				if value.Doc == "" && s.Doc == nil && s.Comment == nil {
					t.Errorf("%s has no doc comment", s.Names[0].Name)
				}
			}
		}
	}
}

// A value spelled through an internal package renders as that package's name
// in the reference, which says nothing to a reader who cannot import it.
func TestNoExportedValueIsSpelledThroughAnInternalPackage(t *testing.T) {
	pkg, _ := documented(t)
	internal := map[string]bool{}
	for _, imp := range pkg.Imports {
		if strings.Contains(imp, "/internal/") {
			internal[imp[strings.LastIndex(imp, "/")+1:]] = true
		}
	}
	if !internal["config"] {
		t.Fatalf("control: the internal imports were not found in %v", pkg.Imports)
	}
	values := slices.Concat(pkg.Consts, pkg.Vars)
	for _, typ := range pkg.Types {
		values = slices.Concat(values, typ.Consts, typ.Vars)
	}
	for _, value := range values {
		ast.Inspect(value.Decl, func(node ast.Node) bool {
			if selector, ok := node.(*ast.SelectorExpr); ok {
				if ident, ok := selector.X.(*ast.Ident); ok && internal[ident.Name] {
					t.Errorf("%v is spelled through %s.%s", value.Names, ident.Name, selector.Sel.Name)
				}
			}
			return true
		})
	}
}
