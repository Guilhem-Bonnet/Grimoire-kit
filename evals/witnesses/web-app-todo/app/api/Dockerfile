FROM golang:1.22-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o /todo-api .

FROM alpine:3.20
COPY --from=build /todo-api /usr/local/bin/todo-api
EXPOSE 8080
CMD ["todo-api"]
