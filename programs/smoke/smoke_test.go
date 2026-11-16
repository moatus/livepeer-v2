package smoke

import "testing"

func TestBumpAddsOne(t *testing.T) {
	if got := Bump(0); got != 1 {
		t.Fatalf("Bump(0) = %d, want 1", got)
	}
}
