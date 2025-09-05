package main

import (
	"testing"

	"github.com/chalk-ai/chalk-go/expr"

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
			WithOutputs("user.name").
			WithOutputs("user.email").
			WithOutputExprs(
				expr.FunctionCall(
					"jaccard_similarity",
					expr.FunctionCall("lower", expr.Col("name")),
					expr.FunctionCall("lower", expr.Col("email")),
				).
					As("user.name_email_sim"),
				expr.DataFrame("transactions").
					Filter(expr.Col("amount").Gt(expr.Float(0.))).
					Agg("count").
					As("user.positive_transaction_count"),
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
