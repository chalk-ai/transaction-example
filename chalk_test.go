package main

import (
	"github.com/chalk-ai/chalk-go/expr"
	"testing"

	"github.com/chalk-ai/chalk-go"
)

func TestChalkClient(t *testing.T) {
	client, err := chalk.NewGRPCClient(t.Context())
	if err != nil {
		t.Fatalf("Failed to create Chalk client: %v", err)
	}
	result, err := client.OnlineQueryBulk(
		t.Context(),
		chalk.OnlineQueryParams{}.
			WithInput("user.id", []int{1}).
			WithOutputs("user.id").
			WithOutputExprs(
				expr.FunctionCall(
					"jaccard_similarity",
					expr.Col("_").Attr("name"),
					expr.Col("_").Attr("email"),
				).
					As("name_email_sim"),
			),
	)
	if err != nil {
		t.Logf("Online query failed (this might be expected if features aren't set up): %v", err)
		return
	}
	row, err := result.GetRow(0)
	if err != nil {
		t.Fatalf("Failed to get row: %v", err)
	}
	for feature, value := range row.Features {
		t.Logf("Feature: %s, Value: %+v", feature, value.Value)
	}

}
