
## Chalk Quickstart

1. Install Chalk

   Install the [Chalk command line tool](https://docs.chalk.ai/cli).
   The Chalk CLI allows you to create, update, and manage your feature
   pipelines directly from your terminal.

   > curl -s -L https://api.chalk.ai/install.sh | sh

2. Create and activate a virtual environment in your project directory

	Creating a virtual environment is a good practice to keep your project 
	dependencies isolated from your system dependencies.

	> python -m venv .venv 
	> source .venv/bin/activate

3. Login or sign up

   Login or signup with Chalk directly from the command line. The
   [`chalk login`](https://docs.chalk.ai/cli/login) command will
   open your browser and create an API token for your local development.

4. Deploy your features

   Deploy your feature pipeline to production. After you've written some
   features and resolvers, use the [`chalk apply`](https://docs.chalk.ai/cli/apply)
   command to deploy your feature pipelines.

5. Query your features

   Query your features directly from the command line with
   [`chalk query`](https://docs.chalk.ai/cli/query) to see that they're
   live and available.

## Chalk Quickstart

| Relevant Links                                                                                                                                                                                                 |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [Chalk Dashboard](#) &#124; [Github Repo](#) &#124; [Chalk Docs](https://docs.chalk.ai) |

## Getting Started

To get up and running with Chalk, we'll:

1. Install the [chalk] CLI tool
2. Install the Chalk plugin for your editor
3. (optional) Set up a virtual environment
4. Login to Chalk
5. Query your features
6. Deploy your features

## 1. Install Chalk

   Install the [Chalk command line tool](https://docs.chalk.ai/cli).
   The Chalk CLI allows you to create, update, and manage your feature
   pipelines directly from your terminal.

   > curl -s -L https://api.chalk.ai/install.sh | sh

## 2. Install the Chalk plugin for your editor

   Install the [Chalk editor plugin](https://docs.chalk.ai/docs/editor-setup)
   for your editor to get diagnostics, navigation, and typechecking as you work.

## 3. (optional) Create and activate a virtual environment in your project directory

	Creating a virtual environment is a good practice to keep your project
	dependencies isolated from your system dependencies.

	> python -m venv .venv
	> source .venv/bin/activate
	> uv sync

## 4. Login or sign up

   Login or signup with Chalk directly from the command line. The
   [`chalk login`](https://docs.chalk.ai/cli/login) command will
   open your browser and create an API token for your local development.

## 5. Query your features

   Query your features directly from the command line with
   [`chalk query`](https://docs.chalk.ai/cli/query) to see that they're
   live and available.

## 6. Deploy your features

   Deploy your feature pipeline to production or to a branch. After
   you've written some new features and resolvers, use the [chalk apply --branch](https://docs.chalk.ai/cli/apply)
   command to deploy your feature pipelines.

   When testing new code, we recommend using a Chalk "branch".
   To deploy to a branch, just add **--branch <branch_name>** to your chalk
   command. To deploy your code to production (which provides dynamic horizontal
   scaling and zero downtime deployments) you'll use [chalk apply].

   To test branch deployments out, run:

    chalk apply --branch "$USER-branch"


   which will deploy your local code to a branch called <your-username>-branch

   The code you just deployed can immediately be queried using the **--branch** flag
   For example:

    chalk query
      --in ...
      --branch "$USER-branch"


## Chalk Quickstart

| Relevant Links                                                                                                                                                                                                 |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [Chalk Dashboard](#) &#124; [Github Repo](#) &#124; [Chalk Docs](https://docs.chalk.ai) |

## Getting Started

To get up and running with Chalk, we'll:

1. Install the [chalk] CLI tool
2. Install the Chalk plugin for your editor
3. (optional) Set up a virtual environment
4. Login to Chalk
5. Query your features
6. Deploy your features

## 1. Install Chalk

   Install the [Chalk command line tool](https://docs.chalk.ai/cli).
   The Chalk CLI allows you to create, update, and manage your feature
   pipelines directly from your terminal.

   > curl -s -L https://api.chalk.ai/install.sh | sh

## 2. Install the Chalk plugin for your editor

   Install the [Chalk editor plugin](https://docs.chalk.ai/docs/editor-setup)
   for your editor to get diagnostics, navigation, and typechecking as you work.

## 3. (optional) Create and activate a virtual environment in your project directory

	Creating a virtual environment is a good practice to keep your project
	dependencies isolated from your system dependencies.

	> python -m venv .venv
	> source .venv/bin/activate
	> uv sync

## 4. Login or sign up

   Login or signup with Chalk directly from the command line. The
   [`chalk login`](https://docs.chalk.ai/cli/login) command will
   open your browser and create an API token for your local development.

## 5. Query your features

   Query your features directly from the command line with
   [`chalk query`](https://docs.chalk.ai/cli/query) to see that they're
   live and available.

## 6. Deploy your features

   Deploy your feature pipeline to production or to a branch. After
   you've written some new features and resolvers, use the [chalk apply --branch](https://docs.chalk.ai/cli/apply)
   command to deploy your feature pipelines.

   When testing new code, we recommend using a Chalk "branch".
   To deploy to a branch, just add **--branch <branch_name>** to your chalk
   command. To deploy your code to production (which provides dynamic horizontal
   scaling and zero downtime deployments) you'll use [chalk apply].

   To test branch deployments out, run:

    chalk apply --branch "$USER-branch"


   which will deploy your local code to a branch called <your-username>-branch

   The code you just deployed can immediately be queried using the **--branch** flag
   For example:

    chalk query
      --in ...
      --branch "$USER-branch"

