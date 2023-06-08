import csv
import hashlib
import os
import subprocess

import frontmatter
import rich
import typer
from gql import Client, gql
from gql.transport.aiohttp import AIOHTTPTransport
from pydantic import BaseModel, parse_obj_as
from pydantic.fields import Field


class Discussion(BaseModel):
    title: str
    body: str
    url: str
    number: int


class Category(BaseModel):
    id: str
    name: str


class Repository(BaseModel):
    id: str
    category: Category = Field(alias="discussionCategory")


def search_discussion(
    client: Client, *, owner: str, repo: str, identifier: str
) -> Discussion | None:
    identifier = identifier.replace('"', '\\"')
    search_query = f'repo:{owner}/{repo} "{identifier}" in:body'
    print(search_query)
    params = {"query": search_query}

    query = gql(
        """
    query SearchDiscussions($query: String!) {
  search(query: $query, type: DISCUSSION, first: 10) {
    nodes {
      ... on Discussion {
        title
        body
        url
        number
      }
    }
  }
}
    """
    )

    result = client.execute(query, variable_values=params)

    discussions = parse_obj_as(list[Discussion], result["search"]["nodes"])

    if len(discussions) > 1:
        raise Exception(f"More than one discussion with identifier: {identifier}")

    if len(discussions) != 1:
        return None

    return discussions[0]


def get_category(client: Client, *, owner: str, repo: str, slug: str) -> Repository:
    params = {"owner": owner, "repo": repo, "slug": slug}

    query = gql(
        """
    query Category($owner: String!, $repo: String!, $slug: String!) {
  repository(owner: $owner, name: $repo) {
    id
    discussionCategory(slug: $slug) {
      id
      name
    }
  }
}
"""
    )

    result = client.execute(query, variable_values=params)

    repository: Repository = parse_obj_as(Repository, result["repository"])
    return repository


def create_discussion(
    client: Client, *, owner: str, repo: str, category: str, body: str, title: str
) -> Discussion:
    repository = get_category(client, owner=owner, repo=repo, slug=category)
    repo_id = repository.id
    category_id = repository.category.id
    params = {
        "repositoryId": repo_id,
        "categoryId": category_id,
        "body": body,
        "title": title,
    }
    query = gql(
        """
    mutation CreateDiscussion(
  $repositoryId: ID!
  $categoryId: ID!
  $body: String!
  $title: String!
) {
  createDiscussion(
    input: {
      repositoryId: $repositoryId
      categoryId: $categoryId
      body: $body
      title: $title
    }
  ) {
    discussion {
      id
      title
      body
      url
      number
    }
  }
}
"""
    )

    result = client.execute(query, variable_values=params)
    return parse_obj_as(Discussion, result["createDiscussion"]["discussion"])


class PostInfo(BaseModel):
    path: str
    permalink: str
    title: str


def list_posts(hugo_root: str):
    posts_csv = bytes.decode(
        subprocess.check_output(["hugo", "list", "all"], cwd=hugo_root), "utf8"
    )
    reader = csv.DictReader(posts_csv.splitlines())
    for row in reader:
        if row["draft"] != "false":
            continue
        path = row["path"]
        permalink = row["permalink"]
        title = row["title"]

        yield PostInfo(path=path, permalink=permalink, title=title)


def make_identifier(url: str) -> str:
    identifier = hashlib.sha1(url.encode("utf8")).hexdigest()
    return f"<!-- {identifier} -->"


def discuss_post(
    post_info: PostInfo, client: Client, owner: str, repo: str, category: str
) -> Discussion:
    identifier = make_identifier(post_info.permalink)

    # First, try and get an existing discussion that matches the post
    discussion = search_discussion(
        client, owner=owner, repo=repo, identifier=identifier
    )
    if discussion:
        return discussion

    body = (
        f"{identifier}\n"
        f"Post link: [{post_info.title}]({post_info.permalink})\n"
        f"\n"
        f"Comment, complain, and ask questions 🙂"
    )

    discussion = create_discussion(
        client,
        owner=owner,
        repo=repo,
        category=category,
        body=body,
        title=post_info.title,
    )

    return discussion


def discuss_posts(hugo_root: str, client: Client, owner: str, repo: str, category: str):
    for post_info in list_posts(hugo_root):
        post_path = os.path.join(hugo_root, post_info.path)
        with open(post_path) as f:
            post = frontmatter.load(f)

        if not post.metadata.get("discuss"):
            continue

        if post.metadata.get("discussAt"):
            continue

        rich.print("Found post to discuss:" , post.metadata)

        discussion = discuss_post(post_info, client, owner, repo, category)

        post.metadata["discussAt"] = discussion.url

        rich.print("Discussing at:", discussion.url)

        with open(post_path, "wb") as f:
            frontmatter.dump(post, f)

            rich.print("Post updated with discussion at:", post_path)


app = typer.Typer()


@app.command()
def main(
    hugo_root: str = typer.Option(...),
    token: str = typer.Option(...),
    owner: str = typer.Option(...),
    repo: str = typer.Option(...),
    category: str = typer.Option(...),
):
    # Select your transport with a defined url endpoint
    transport = AIOHTTPTransport(
        url="https://api.github.com/graphql",
        headers={"Authorization": f"bearer {token}"},
    )

    # Create a GraphQL client using the defined transport
    client = Client(transport=transport, fetch_schema_from_transport=True)

    discuss_posts(
        hugo_root=hugo_root, client=client, owner=owner, repo=repo, category=category
    )


if __name__ == "__main__":
    app()
