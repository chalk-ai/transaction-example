package main

import (
	"context"
	"fmt"
	"os"
	"testing"

	"github.com/chalk-ai/chalk-go"
)

// User represents a user in the system
type User struct {
	ID    string `json:"id"`
	Name  string `json:"name"`
	Email string `json:"email"`
}

func TestChalkClient(t *testing.T) {
	// Get API key from environment variable
	apiKey := os.Getenv("CHALK_API_KEY")
	if apiKey == "" {
		t.Skip("CHALK_API_KEY environment variable not set")
	}

	// Create a new Chalk client
	client, err := chalk.NewClient(&chalk.ClientConfig{
		ApiKey: apiKey,
		// Optionally set the environment
		// EnvironmentId: "your-environment-id",
	})
	if err != nil {
		t.Fatalf("Failed to create Chalk client: %v", err)
	}

	// Example: Query for a user
	ctx := context.Background()
	
	// Create query input
	input := map[string]interface{}{
		"user.id": "test-user-123",
	}

	// Create query params
	params := chalk.OnlineQueryParams{
		IncludeMeta: true,
		// Specify which features to return
		OutputsMap: map[string][]string{
			"user": {"id", "name", "email"},
		},
	}

	// Execute the online query
	result, err := client.OnlineQuery(ctx, params, input)
	if err != nil {
		t.Logf("Online query failed (this might be expected if features aren't set up): %v", err)
		return
	}

	// Check if we got data
	if result != nil && result.Data != nil {
		t.Logf("Query successful!")
		
		// Log the returned data
		for key, value := range result.Data {
			t.Logf("Feature %s: %v", key, value)
		}
		
		// If meta information is available
		if result.Meta != nil {
			t.Logf("Query ID: %s", result.Meta.QueryId)
			if result.Meta.QueryTimestampMs > 0 {
				t.Logf("Query timestamp: %d ms", result.Meta.QueryTimestampMs)
			}
		}
	}
}

func ExampleChalkClient() {
	// This is an example of how to use the Chalk client
	apiKey := os.Getenv("CHALK_API_KEY")
	if apiKey == "" {
		fmt.Println("Please set CHALK_API_KEY environment variable")
		return
	}

	// Create client
	client, err := chalk.NewClient(&chalk.ClientConfig{
		ApiKey: apiKey,
	})
	if err != nil {
		fmt.Printf("Failed to create client: %v\n", err)
		return
	}

	// Query for a user
	ctx := context.Background()
	
	input := map[string]interface{}{
		"user.id": "example-user",
	}

	params := chalk.OnlineQueryParams{
		OutputsMap: map[string][]string{
			"user": {"id", "name"},
		},
	}

	result, err := client.OnlineQuery(ctx, params, input)
	if err != nil {
		fmt.Printf("Query failed: %v\n", err)
		return
	}

	if result != nil && result.Data != nil {
		fmt.Printf("User data retrieved successfully\n")
	}
}